/*
image-sync-operator

Copyright 2026 Apex Northwest

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
 */

mod config;
mod imagesync;
use config::Config;
use config::read_config_file;
use futures::StreamExt;
use imagesync::ImageSync;
use k8s_openapi::api::batch::v1::{CronJob, CronJobSpec, CronJobStatus, Job, JobSpec, JobStatus};
use kube::{
    Api, Client, ResourceExt,
    api::ListParams,
    runtime::controller::{Action, Controller},
};
use kube_lease_manager::LeaseManagerBuilder;
use once_cell::sync::Lazy;
use std::{sync::Arc, time::Duration};
use regex::Regex;

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
        let namespaces = if ! CONFIG.watched_namespaces.contains(&namespace.to_string()) {
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
    println!("Operator is starting with the following configuration: {:?}", *CONFIG);

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
    let skopeo_image = &CONFIG.skopeo.image;
    let skopeo_pull_policy = &CONFIG.skopeo.image_pull_policy;
    let skopeo_ca_trust_bundle = &CONFIG.skopeo.ca_trust_bundle;
    println!("reconcile request: {}", obj.name_any());
    println!("spec: {:?}", obj.spec);
    let imagesyncs = Api::<ImageSync>::namespaced(
        Client::try_default().await.unwrap(),
        &obj.namespace().unwrap(),
    );
    let jobs = Api::<Job>::namespaced(
        Client::try_default().await.unwrap(),
        &obj.namespace().unwrap(),
    );
    let secrets = Api::<k8s_openapi::api::core::v1::Secret>::namespaced(
        Client::try_default().await.unwrap(),
        &obj.namespace().unwrap(),
    );

    let basename = if obj.metadata.name.iter().len() > 50 {
        obj.metadata.name.clone().unwrap()[0..50].to_string()
    } else {
        obj.metadata.name.clone().unwrap()
    };
    let joblist = jobs
        .list(
            &ListParams::default().labels(&format!("imagesync.apexnw.dev/imagesync={}", obj.metadata.name.clone().unwrap())),
        )
        .await
        .unwrap();

    // Flag if the config has changed since we last touched it. This is used in several places to determine if we should patch the object or not.
    let config_changed = obj.status.as_ref().map_or(true, |s| !serde_json::to_string(&s.last_applied_config).unwrap_or_default().eq(&serde_json::to_string(&obj.spec).unwrap_or_default()));
    
    // Acceptance checks
    let mut source_secret_okay = true;
    let mut source_secret_message = String::new();
    let mut dest_secret_okay = true;
    let mut dest_secret_message = String::new();
    let mut cron_schedule_okay = true;
    let mut source_image_okay = true;
    let mut dest_image_okay = true;

    // Acceptance check for the source secret
    if obj.spec.source.registry_login_secret.is_some() {
        let secret_name = obj.spec.source.registry_login_secret.as_ref().unwrap();
        match secrets.get(secret_name).await {
            Ok(secret) => {
                println!("Source secret {} exists", secret_name);
                if secret.type_ != Some(String::from("kubernetes.io/dockerconfigjson")) {
                    println!("Source secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                    source_secret_okay = false;
                    source_secret_message = format!("Source secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                }
            }
            Err(e) => {
                println!("Source secret {} does not exist: {}", secret_name, e);
                source_secret_okay = false;
                source_secret_message = format!("Source secret {} does not exist", secret_name);
            }
        }
    }

    // Acceptance check for the destination secret
    if obj.spec.destination.registry_login_secret.is_some() {
        let secret_name = obj.spec.destination.registry_login_secret.as_ref().unwrap();
        match secrets.get(secret_name).await {
            Ok(secret) => {
                println!("Destination secret {} exists", secret_name);
                if secret.type_ != Some(String::from("kubernetes.io/dockerconfigjson")) {
                    println!("Destination secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                    dest_secret_okay = false;
                    dest_secret_message = format!("Destination secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                }
            }
            Err(e) => {
                println!("Destination secret {} does not exist: {}", secret_name, e);
                dest_secret_okay = false;
                dest_secret_message = format!("Destination secret {} does not exist", secret_name);
            }
        }
    }

    // Acceptance check for the cron schedule
    if obj.spec.cron_schedule.is_some() {
        let schedule = obj.spec.cron_schedule.as_ref().unwrap();
        if !Regex::new(r"^\s*\#?\s*(?:(?:(?'mins'[0-5]?\d)(?:[-,](?&mins))*)|\*)(?:/\d{1,2})?\s+(?:(?:(?'hours'(?:2[0-3]|[01]?\d))(?:[-,](?&hours))*)|\*)(?:/\d{1,2})?\s+(?:(?:(?'dmon'(?:3[01]|[12]?\d))(?:[-,](?&dmon))*)|\*)(?:/\d{1,2})?\s+(?:(?:(?'mon'(?:1[0-2]|[1-9]))(?:[-,](?&mon))*)|\*)(?:/\d{1,2})?\s+(?:(?:(?'dow'(?:[0-6]|\b(?:mon|tue|wed|thu|fri|sat|sun)\b))(?:[-,](?&dow))*)|\*)(?:/\d{1,2})?\s+.+$").unwrap().is_match(schedule) {
            println!("Cron schedule {} is valid", schedule);
        } else {
            println!("Cron schedule {} is invalid", schedule);
            cron_schedule_okay = false;
        }
    }

    // Acceptance check for the source image URL
    if !Regex::new(r"^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])/[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+(:[a-zA-Z0-9_\-\.]{1,128}|@sha256:[a-f0-9]+)").unwrap().is_match(&obj.spec.source.image) {
        println!("Source image URL {} is invalid", obj.spec.source.image);
        source_image_okay = false;
    }

    // Acceptance check for the destination image URL
    if !Regex::new(r"^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])/[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+(:[a-zA-Z0-9_\-\.]{1,128})").unwrap().is_match(&obj.spec.destination.image) {
        println!("Destination image URL {} is invalid", obj.spec.destination.image);
        dest_image_okay = false;
    }

    // Determine if the ImageSync configuration is accepted and render the message accordingly
    let mut accepted = true;
    let mut accepted_message = String::from("ImageSync configuration is valid");
    if !source_secret_okay || !dest_secret_okay || !cron_schedule_okay || !source_image_okay || !dest_image_okay {
        accepted = false;
        accepted_message = String::from("ImageSync configuration is invalid: ");
        if !source_secret_okay {
            accepted_message.push_str(&format!("Source secret error: {}. ", source_secret_message));
        }
        if !dest_secret_okay {
            accepted_message.push_str(&format!("Destination secret error: {}. ", dest_secret_message));
        }
        if !cron_schedule_okay {
            accepted_message.push_str("Cron schedule is invalid. ");
        }
        if !source_image_okay {
            accepted_message.push_str("Source image URL is invalid. ");
        }
        if !dest_image_okay {
            accepted_message.push_str("Destination image URL is invalid. ");
        }
    }

    if accepted && obj.status.as_ref().map_or(false, |s| s.accepted == true) {
        println!("ImageSync {} is already accepted", obj.name_any());
    } else if !accepted && obj.status.as_ref().map_or(false, |s| s.accepted == false) && !config_changed {
        println!("ImageSync {} is already rejected and hasn't changed", obj.name_any());
    } else {
        println!("Updating status for ImageSync {} to accepted={}", obj.name_any(), accepted);
        let mut patched_obj = obj.as_ref().clone();
        patched_obj.status = Some(imagesync::ImageSyncStatus {
            accepted: accepted,
            message: accepted_message,
            ready: false,
            last_applied_config: obj.spec.clone(),
            last_completion_time: None,
        });
        let patch_params = kube::api::PatchParams::apply("image-sync-operator").force();
        let patch = kube::api::Patch::Apply(&patched_obj);
        match imagesyncs.patch_status(&obj.name_any(), &patch_params, &patch).await {
            Ok(_) => {
                println!("Successfully updated status for ImageSync {}", obj.name_any());
            }
            Err(e) => {
                println!("Failed to update status for ImageSync {}: {}", obj.name_any(), e);
            }
        }
        if cron_schedule_okay && source_image_okay && dest_image_okay {
            // Fast requeue if the only errors are secrets
            return Ok(Action::requeue(Duration::from_secs(10)));
        } else {
            // Slow requeue if the errors are syntax validation failures
            return Ok(Action::requeue(Duration::from_secs(3600)));
        }
    }
    // End of acceptance checks

    // TODO: If the job is marked as ready=true, we don't care about the job as long as the spec matches the last_applied_config.
    // If the spec doesn't match, we need to set ready=false and requeue with a short wait.
    // TODO: Rather than just assuming that the job is correct we need to get the job and compare it to the configured spec.
    // If the job spec is wrong, we should delete it and requeue the reconcile.
    // TODO: When the job gets created, we need to set the last_applied_config to the current spec and set ready=false and accepted=true.
    // TODO: Check if the config has a schedule first, if it does and ready=true, create a cronjob instead of a job.
    // TODO: If the config has a schdule, but no cronjob, and the status is ready=false, then run a regular job first to get it into ready state, then requeue.
    // TODO: If the status is ready=false, but the job exists and is not finished, then requeue with a short wait.
    // TODO: If the status is ready=false, but the job exists and is finished, check if the job was successful, if it was, set ready=true and requeue with a short wait.
    // If the job finished with an error, set ready=false and set a message to that effect. Then requeue with the long wait, as this is likely a configuration error.
    // TODO: If there is a cron schedule set, we must ALWAYS check the cronjob's config to ensure it matches both the spec and last_applied_spec. If any of these mismatch, remove the cronjob (and job if it exists) and requeue.
    if joblist.items.len() == 0 {
        println!("Creating job for imagesync: {}", obj.name_any());
        let mut containers = Vec::<k8s_openapi::api::core::v1::Container>::new();
        let mut command: Vec<String> = Vec::<String>::new();
        command.push("/bin/bash".to_string());
        command.push("-c".to_string());
        command.push(r#"cat >> /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem <<EOF
{ca_trust_bundle}
EOF
skopeo copy {preserve_digests} {all_architectures} {src_options} {dest_options} docker://{src} docker://{dest}"#
                    .replace("{ca_trust_bundle}", skopeo_ca_trust_bundle.as_ref().map_or("", |s| s))
                    .replace("{preserve_digests}", if obj.spec.preserve_digests.unwrap_or(false) { "--preserve-digests" } else { "" })
                    .replace("{all_architectures}", if obj.spec.all_architectures.unwrap_or(false) { "--all" } else { "" })
                    .replace("{src_options}", if obj.spec.source.registry_login_secret.is_some() { "--src-authfile /creds/src/.dockerconfigjson" } else { "" })
                    .replace("{dest_options}", if obj.spec.destination.registry_login_secret.is_some() { "--dest-authfile /creds/dest/.dockerconfigjson" } else { "" })
                    .replace("{src}", &obj.spec.source.image)
                    .replace("{dest}", &obj.spec.destination.image));
        containers.push(k8s_openapi::api::core::v1::Container {
            name: "skopeo".to_string(),
            image: Some(skopeo_image.clone()),
            image_pull_policy: Some(skopeo_pull_policy.clone()),
            command: Some(command),
            volume_mounts: Some(vec![
                k8s_openapi::api::core::v1::VolumeMount {
                    name: "creds-src".to_string(),
                    mount_path: "/creds/src".to_string(),
                    read_only: Some(true),
                    ..Default::default()
                },
                k8s_openapi::api::core::v1::VolumeMount {
                    name: "creds-dest".to_string(),
                    mount_path: "/creds/dest".to_string(),
                    read_only: Some(true),
                    ..Default::default()
                },
            ]),
            ..Default::default()
        });
        let job = Job {
            metadata: kube::api::ObjectMeta {
                name: Some(format!("imagesync-{}", basename)),
                labels: Some(std::collections::BTreeMap::from([
                    ("imagesync.apexnw.dev/imagesync".to_string(), basename.clone())
                ])),
                ..Default::default()
            },
            spec: Some(JobSpec {
                template: k8s_openapi::api::core::v1::PodTemplateSpec {
                    metadata: Some(k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta {
                        labels: Some(std::collections::BTreeMap::from([
                            ("imagesync.apexnw.dev/imagesync".to_string(), basename.clone())
                        ])),
                        ..Default::default()
                    }),
                    spec: Some(k8s_openapi::api::core::v1::PodSpec {
                        containers: containers,
                        volumes: Some(vec![
                            k8s_openapi::api::core::v1::Volume {
                                name: "creds-src".to_string(),
                                secret: obj.spec.source.registry_login_secret.as_ref().map(|secret_name| k8s_openapi::api::core::v1::SecretVolumeSource {
                                    secret_name: Some(secret_name.clone()),
                                    ..Default::default()
                                }),
                                ..Default::default()
                            },
                            k8s_openapi::api::core::v1::Volume {
                                name: "creds-dest".to_string(),
                                secret: obj.spec.destination.registry_login_secret.as_ref().map(|secret_name| k8s_openapi::api::core::v1::SecretVolumeSource {
                                    secret_name: Some(secret_name.clone()),
                                    ..Default::default()
                                }),
                                ..Default::default()
                            },
                        ]),
                        restart_policy: Some("Never".to_string()),
                        ..Default::default()
                    }),
                },
                backoff_limit: Some(4),
                ..Default::default()
            }),
            status: None,
        };
        jobs.create(&Default::default(), &job).await.unwrap();
    } else {
        println!("Job already exists for imagesync: {}", obj.name_any());
    }
    // Sleep for 60sec to let job finish for testing
    tokio::time::sleep(Duration::from_secs(60)).await;
    let job = jobs.get(&format!("imagesync-{}", basename)).await.unwrap();
    println!("Job status: {:?}", job.status);

    Ok(Action::requeue(Duration::from_secs(3600)))
}

fn error_policy(_object: Arc<ImageSync>, _err: &Error, _ctx: Arc<()>) -> Action {
    Action::requeue(Duration::from_secs(30))
}
