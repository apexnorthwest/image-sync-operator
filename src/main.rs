// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
//! image-sync-operator is a Kubernetes operator that synchronizes container images between registries based on the ImageSync custom resource definition (CRD).
//! 
//! Installing the operator is typically done via helm by pulling the chart from the public oci repository.
//! You can also find the charts in the legacy helm registry format [here](https://apexnorthwest.github.io/image-sync-operator/charts/).
//! 
//! To install the operator in single-namespace mode with the default settings you would do the following:
//! ```sh
//! helm repo add apexnw oci://ghcr.io/apexnorthwest/charts
//! helm install image-sync-operator apexnw/image-sync-operator -n image-sync-operator --create-namespace
//! ```
//! 
//! To fully customize the configuration you would pass a values.yaml files like so:
//! ```yaml
//! ---
//! # Settings for the operator itself. This is the container that runs the operator code and watches for ImageSync CRs.
//! operator:
//!   # Image url to pull from
//!   image: 
//!     repository: "ghcr.io/apexnorthwest/image-sync-operator"
//!     tag: "0.1.0"
//!     pullPolicy: "IfNotPresent"
//!   # Enable for debug logging. This is not recommended for production use but if you even need to open an issue
//!   # on the project then we will expect you to provide logs with this enabled.
//!   debug: false
//! service_account:
//!   # Should this chart create a service account?
//!   # If false, the service account named below must already exist
//!   create: true
//!   # Name of the service account to use/create
//!   # Defaults to release name if not set
//!   name: ""
//! # Settings related to the skopeo image used by the operator
//! # Technically, any image that contains skopeo can be used, but we prefer the official one
//! # Notably, the ca trust must be in /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem, so
//! # if you use an image based on a different OS, it's likely it will have tls problems.
//! skopeo:
//!   # Image url to pull from
//!   image:
//!     repository: "quay.io/skopeo/stable"
//!     tag: "latest"
//!     pullPolicy: "IfNotPresent"
//!   # Optional CA trust bundle to use when verifying TLS connections to registries
//!   # This should be a PEM encoded bundle of CA certificates, and will be appended
//!   # to the system CA trust bundle in the skopeo container. This is generally mandatory when
//!   # working with private, authenticated registries.
//!   ca_trust_bundle: |-
//!     -----BEGIN CERTIFICATE-----
//!     ***********omitted***********
//!     -----END CERTIFICATE-----
//! # Settings related to the operator's access
//! rbac:
//!   # Should this chart create the cluster role `image-sync-operator`?
//!   # This must be set to true if cluster_scoped is true, otherwise it is optional.
//!   create_cluster_role: false
//!   # Will this operator need to be cluster scoped?
//!   # If true, the operator will watch all namespaces and require cluster role permissions.
//!   cluster_scoped: false
//!   # When not in cluster scoped mode, which namespaces should the operator watch?
//!   # If empty, the operator will watch the namespace it is deployed in only.
//!   # This will be ignored if cluster_scoped is true.
//!   # The operator service account will need to have a rolebinding in these namespaces to be able to watch for ImageSync CRs.
//!   # The Role can be copied from the install namespace, or if create_cluster_role is true then you can bind that cluster role into the namespace.
//!   watched_namespaces:
//!     - "image-sync-operator"
//!     - "default"
//!     - "some-other-application"
//! ```
//! 
//! You would then create ImageSync CRs in that same namespace (as in this example we've installed the operator in restricted mode, as is the default)
//! 
//! The following is an example of an ImageSync:
//! ```yaml
//! ---
//! apiVersion: imagesync.apexnw.dev/v1alpha1
//! kind: ImageSync
//! metadata:
//!   name: alpine-latest
//!   namespace: image-sync-operator
//! spec:
//!   source:
//!     # Full URI of the source image as would be passed to skopeo (minus the docker:// prefix, which is presumed)
//!     # Note: You cannot use docker short names for images. You must use full docker.io paths, as skopeo cannot know what registry to use for short names. This applies to both source and dest.
//!     # This can also be an @ hash reference. Destination however, cannot be as you must tag the pushed image to something.
//!     image: "docker.io/library/alpine:latest"
//!     # Optional: The name of a Secret in the same namespace of type kubernetes.io/dockerconfigjson used to authenticate to the source registry.
//!     registryLoginSecret: "dockerhub"
//!   destination:
//!     # Full URI of the destination image as would be passed to skopeo (minus the docker:// prefix, which is presumed)
//!     image: "my-private-registry.example.com/library/alpine:latest"
//!     # Optional: The name of a Secret in the same namespace of type kubernetes.io/dockerconfigjson used to authenticate to the destination registry.
//!     registryLoginSecret: "private-registry"
//!   # Optional: If you want to rerun the sync on a schedule after the initial sync. Uses CronJob format. If unset, the sync only runs once. 
//!   cronSchedule: "*/15 * * * *"
//!   # Optional: If true, all architectures will be copied. If false, only the architecture of the operator pod will be copied. Defaults to false. This is the same as passing --all to skopeo.
//!   allArchitectures: true
//!   # Optional: If true, the destination image will be tagged with the digest of the source image. If false, the destination image will be tagged with the tag of the source image. Defaults to false. This is the same as passing --preserve-digests to skopeo
//!   # Notably, if this is set to true, and allArchitectures is set to false, multi-arch images will fail to copy and the sync will fail.
//!   preserveDigests: true
//!   # Optional: If you want to pass extra arguments to skopeo, you can do so here. This is a string that will be split on whitespace and passed to skopeo as-is. For example, if you want to pass --insecure-policy to skopeo, you would set this to "--insecure-policy".
//!   # Note that this is not a list of arguments, but a single string that will be split on whitespace. If it needs to have spaces in an argument (which it shouldn't), you will need to use single quotes inside the string.
//!   extraSkopeoArguments: ""
//! ```
//! 
//! This project is not meant to be used as a library or installed via cargo. As a result, all contained functions and modules documented within this site are 
//! written for the use of contributors and not users of the operator. 

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

/// Catchall error type for the operator.
#[derive(thiserror::Error, Debug)]
pub enum Error {}

pub type Result<T, E = Error> = std::result::Result<T, E>;

/// Global config object. This is ready lazily from /config/config.yaml and is used to set global config options in the operator.
pub static CONFIG: Lazy<Config> =
    Lazy::new(|| read_config_file().expect("Failed to read config file"));

/// Entrypoint for the operator. This main function sets up the leader election, watches for ImageSync CRD changes,
/// and calls the reconciler when a change is detected. It uses tokio as the async runtime and is not meant to be called directly.
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

/// This reconcile funtion is the main logical driver of the operator. It is called whenever an event is generated related to an ImageSync CRD.
/// This runs as a single listener in global or single-namespace mode. In multi-namespace mode it will run as a listener on each namespace it monitors.
/// Notably, this function usually only performs a single action when called, even if multiple actions are called for. This is because we want the function
/// to be as idempotent as possible. It also ensures that no matter what state the ImageSync is in, it will always progress towards the correct state.
/// 
/// This function is not meant to be called from anywhere other than the controller thread.
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

    // Manage CronJobs when needed
    if obj.spec.cron_schedule.is_some() {
        let cronjob = cronjobs::get_cronjob_for_imagesync(&obj, &client)
            .await
            .unwrap();
        if cronjob.is_none() {
            cronjobs::create_cronjob(&obj, &CONFIG.skopeo, &client)
                .await
                .unwrap();
            // Fast requeue to watch the cronjob status
            return Ok(Action::requeue(Duration::from_secs(5)));
        } else {
            // CronJob exists, assert that the spec is correct with what's in the CR.
            if !cronjobs::is_cronjob_spec_correct(&obj, cronjob.as_ref().unwrap(), &CONFIG.skopeo).await {
                // CronJob spec is incorrect, delete it and recreate it.
                cronjobs::delete_cronjob(cronjob.as_ref().unwrap(), &client)
                    .await
                    .unwrap();
                cronjobs::create_cronjob(&obj, &CONFIG.skopeo, &client)
                    .await
                    .unwrap();
                // Normal requeue
                return Ok(Action::requeue(Duration::from_secs(30)));
            } else {
                // CronJob spec is correct, update the status with the last successful run time.
                let last_success = cronjobs::cronjob_get_last_success(obj.as_ref().clone()).await;
                reconciler::update_status(
                    obj.as_ref().clone(),
                    true,
                    true,
                    String::from("ImageSync CronJob is running"),
                    last_success,
                    &client,
                )
                .await
                .unwrap();
            }
        }
    }

    // Fallback requeue for periodic check (5min)
    Ok(Action::requeue(Duration::from_secs(300)))
}

/// This function is called when the reconciler encounters an error. In this case, we simply retry the reconcile after 30 seconds.
/// Because the reconciler is idempotent, this is safe to do. The error is logged by the controller runtime.
fn error_policy(_object: Arc<ImageSync>, _err: &Error, _ctx: Arc<()>) -> Action {
    eprintln!("Reconcile error: {:?} on {:?}", _err, _object.metadata.name);
    Action::requeue(Duration::from_secs(30))
}
