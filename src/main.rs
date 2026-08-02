// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
/*
This is the entrypoint for the operator.
The code in this file ensures we have a leader lock, sets up the listener for the ImageSync CRD, and calls the reconciler when a change is detected.
The reconciler is the primary logic for the operator and leverages functions in other modules to perform the actual work of the operator.
*/

mod config;
mod cronjobs;
mod imagesync;
mod jobs;
mod reconciler;

use crate::config::Config;
use crate::config::read_config_file;
use crate::imagesync::ImageSync;

use futures::StreamExt;
// import jiff via openapi since that's why we use it at all
use k8s_openapi::jiff::Timestamp;
use kube::{
    Api, Client,
    runtime::controller::{Action, Controller},
};
use kube_lease_manager::LeaseManagerBuilder;
use once_cell::sync::Lazy;
use std::{sync::Arc, time::Duration};

#[derive(thiserror::Error, Debug)]
pub enum Error {}

pub type Result<T, E = Error> = std::result::Result<T, E>;

pub static CONFIG: Lazy<Config> =
    Lazy::new(|| read_config_file().expect("Failed to read config file"));

#[tokio::main]
async fn main() -> Result<(), kube::Error> {
    let client = Client::try_default().await?;
    let namespace = client.default_namespace();

    let manager_client = Client::try_default().await?;
    let manager = LeaseManagerBuilder::new(manager_client, "image-sync-operator")
        .with_namespace(namespace)
        .build()
        .await
        .unwrap();

    let controller_client = Client::try_default().await?;
    let mut imagesyncs: Vec<Api<ImageSync>>;
    if CONFIG.cluster_scope {
        println!("Operator is running in cluster-scoped mode");
        imagesyncs = vec![Api::<ImageSync>::all(controller_client)];
    } else {
        println!("Operator is running in namespace-scoped mode");
        imagesyncs = Vec::<Api<ImageSync>>::new();
        let namespaces = if !CONFIG.watched_namespaces.contains(&namespace.to_string()) {
            vec![namespace.to_string()]
        } else {
            CONFIG.watched_namespaces.clone()
        };
        for ns in namespaces.iter() {
            let api = Api::<ImageSync>::namespaced(controller_client.clone(), ns);
            imagesyncs.push(api);
        }
    }
    println!("Operator has {} watchers", imagesyncs.len());
    println!(
        "Operator is starting with the following configuration: {:?}",
        *CONFIG
    );

    // Start manager in watching mode and get back status channel and task handler.
    let (mut channel, task) = manager.watch().await;

    // Watch on the channel for lock state changes.
    tokio::select! {
        _ = channel.changed() => {
            let lock_state = *channel.borrow_and_update();

            if lock_state {
                // Do something useful as a leader
                println!("Operator has become the leader");
                if CONFIG.cluster_scope {
                    println!("Starting controller for cluster-scoped ImageSyncs");
                    Controller::new(imagesyncs[0].clone(), Default::default())
                        .run(reconcile, error_policy, Arc::new(()))
                        .for_each(|_| futures::future::ready(())).await;
                } else {
                    for ns in imagesyncs.iter() {
                        println!("Starting controller for namespace: {}", ns.namespace().unwrap());
                        Controller::new(ns.clone(), Default::default())
                            .run(reconcile, error_policy, Arc::new(()))
                            .for_each(|_| futures::future::ready(())).await;
                    }
                }
            }
        }
        _ = tokio::time::sleep(Duration::from_secs(60)) => {
            println!("Unable to get lock during 60s");
        }
    }
    println!("Operator is shutting down");

    // Explicitly close the control channel
    drop(channel);

    // Wait for the finish of the manager and get it back
    let _manager = tokio::join!(task).0.unwrap();

    Ok(())
}

async fn reconcile(obj: Arc<ImageSync>, _ctx: Arc<()>) -> Result<Action> {
    // Configure K8s api connection
    let client = Client::try_default().await.unwrap();

    // Check if the config has changed or is new
    if reconciler::has_config_changed(&obj).await.unwrap() {
        // Spec has changed, reset status
        reconciler::reset_to_not_accepted(&obj, &client)
            .await
            .unwrap();
        // Run acceptance checks on the new spec and update status accordingly
        if reconciler::acceptance_checks(&obj, &client).await.unwrap() {
            // Immediate requeue to process the new spec
            return Ok(Action::requeue(Duration::ZERO));
        } else {
            // Longer requeue due to acceptance failure
            return Ok(Action::requeue(Duration::from_secs(30)));
        }
    }

    // Check if we are Accepted or not
    if !obj.status.as_ref().unwrap().accepted {
        // Run acceptance checks
        if reconciler::acceptance_checks(&obj, &client).await.unwrap() {
            // Immediate requeue to process the new spec
            return Ok(Action::requeue(Duration::ZERO));
        } else {
            // Longer requeue due to acceptance failure
            return Ok(Action::requeue(Duration::from_secs(30)));
        }
    }

    // Process the CR when it's both accepted and ready
    if obj.status.as_ref().unwrap().ready {
        if obj.spec.cron_schedule.is_some() {
            // TODO: Implement cron job features
        } else {
            // If the CR is ready and does not have a cron schedule, there's nothing to do. Do a very long requeue.
            return Ok(Action::requeue(Duration::from_secs(3600)));
        }
    }

    // If the CR is accepted but not ready, we need to create a one-shot job to process it. We always fire a one-shot, even when the job is scheduled.
    let job = jobs::get_job_for_imagesync(&obj, &client).await.unwrap();

    // Create job when absent
    if job.is_none() {
        jobs::create_job(&obj, &CONFIG.skopeo, &client)
            .await
            .unwrap();
        // Fast requeue to watch the job status
        return Ok(Action::requeue(Duration::from_secs(5)));
    }

    // If we get here, the job exists. Check its status.
    if jobs::is_job_complete(job.as_ref().unwrap()).await {
        // Job is not running, check if it succeeded or failed
        if jobs::is_job_failed(job.as_ref().unwrap()).await {
            // Job failed. Check how long ago the job failed. If it was more than 10 minutes ago we will delete it to retry.
            let last_completion_time = job
                .as_ref()
                .unwrap()
                .status
                .as_ref()
                .unwrap()
                .completion_time
                .clone()
                .unwrap();
            if last_completion_time
                .0
                .duration_until(Timestamp::now())
                .as_mins()
                > 10
            {
                // Delete the job to retry
                jobs::delete_job(job.as_ref().unwrap(), &client)
                    .await
                    .unwrap();
                // Fast requeue to watch the job status
                reconciler::update_status(
                    obj.as_ref().clone(),
                    true,
                    false,
                    String::from("ImageSync job failed, retrying"),
                    None,
                    &client,
                )
                .await
                .unwrap();
                return Ok(Action::requeue(Duration::from_secs(5)));
            } else {
                reconciler::update_status(
                    obj.as_ref().clone(),
                    true,
                    false,
                    String::from("ImageSync job failed"),
                    None,
                    &client,
                )
                .await
                .unwrap();
            }
        } else {
            // Job succeeded. Update the status to ready.
            reconciler::update_status(
                obj.as_ref().clone(),
                true,
                true,
                String::from("ImageSync job completed successfully"),
                Some(
                    job.as_ref()
                        .unwrap()
                        .status
                        .as_ref()
                        .unwrap()
                        .completion_time
                        .clone()
                        .unwrap(),
                ),
                &client,
            )
            .await
            .unwrap();
            // Fast requeue since we don't know if we need to create a CronJob or not.
            return Ok(Action::requeue(Duration::from_secs(5)));
        }
    } else {
        // Job is still running, update the status to not ready.
        reconciler::update_status(
            obj.as_ref().clone(),
            true,
            false,
            String::from("ImageSync job is running"),
            None,
            &client,
        )
        .await
        .unwrap();
        // Fast requeue to watch the job status
        return Ok(Action::requeue(Duration::from_secs(5)));
    }

    // Fallback requeue for periodic check (5min)
    Ok(Action::requeue(Duration::from_secs(300)))
}

fn error_policy(_object: Arc<ImageSync>, _err: &Error, _ctx: Arc<()>) -> Action {
    Action::requeue(Duration::from_secs(30))
}
