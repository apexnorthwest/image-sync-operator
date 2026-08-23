// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
//! This module contains the helper functions to manage the CronJobs used by scheduled image syncs.

use crate::config::SkopeoConfig;
use crate::imagesync::ImageSync;
use k8s_openapi::api::batch::v1::{CronJob, CronJobSpec, JobSpec, JobTemplateSpec};
use k8s_openapi::api::core::v1::{
    Container, PodSpec, PodTemplateSpec, SecretVolumeSource, Volume, VolumeMount,
};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{ObjectMeta, Time};
use kube::{Api, Client};
use std::collections::BTreeMap;

/// Delete the given job from the Kubernetes cluster.
pub async fn delete_cronjob(
    job: &CronJob,
    client: &Client,
) -> Result<(), Box<dyn std::error::Error>> {
    let jobs: Api<CronJob> =
        Api::namespaced(client.clone(), job.metadata.namespace.as_ref().unwrap());
    match jobs
        .delete(
            job.metadata.name.clone().unwrap().as_str(),
            &Default::default(),
        )
        .await
    {
        Ok(_) => {
            println!("Deleted cronjob {}", job.metadata.name.clone().unwrap());
            Ok(())
        }
        Err(e) => {
            println!(
                "Failed to delete cronjob {}: {}",
                job.metadata.name.as_ref().unwrap(),
                e
            );
            Err(Box::new(e))
        }
    }
}

/// Get the CronJob associated with the given ImageSync CR, if it exists.
///
/// Returns a CronJob object if it finds one, None if it does not, or Errors out if there are more than one job. This should never happen unless something has gone quite wrong.
pub async fn get_cronjob_for_imagesync(
    obj: &ImageSync,
    client: &Client,
) -> Result<Option<CronJob>, Box<dyn std::error::Error>> {
    let jobs: Api<CronJob> = Api::namespaced(
        client.clone(),
        obj.metadata.namespace.as_ref().unwrap().as_str(),
    );
    let basename = if obj.metadata.name.as_ref().unwrap().len() > 50 {
        obj.metadata.name.as_ref().unwrap()[0..50].to_string()
    } else {
        obj.metadata.name.as_ref().unwrap().to_string()
    };
    let cronjoblist = jobs
        .list(
            &kube::api::ListParams::default()
                .labels(&format!("imagesync.apexnw.dev/imagesync-cron={}", basename)),
        )
        .await?;
    if cronjoblist.items.len() == 1 {
        Ok(Some(cronjoblist.items[0].clone()))
    } else if cronjoblist.items.len() > 1 {
        eprintln!(
            "Found multiple cronjobs for ImageSync {}. This should not happen.",
            basename
        );
        Err(Box::new(std::io::Error::other(
            "Multiple cronjobs found for ImageSync",
        )))
    } else {
        Ok(None)
    }
}

/// Renders a CronJob object from the ImageSync spec and a config bundle.
pub async fn generate_cronjob_object(
    obj: &ImageSync,
    debug: bool,
    config: &SkopeoConfig,
) -> CronJob {
    let basename = if obj.metadata.name.as_ref().unwrap().len() > 50 {
        obj.metadata.name.as_ref().unwrap()[0..50].to_string()
    } else {
        obj.metadata.name.as_ref().unwrap().to_string()
    };
    let mut containers = Vec::<Container>::new();
    let mut command: Vec<String> = Vec::<String>::new();
    command.push("/bin/bash".to_string());
    command.push("-c".to_string());
    command.push(r#"cat >> /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem <<EOF
{ca_trust_bundle}
EOF
skopeo copy --src-cert-dir /etc/pki/ca-trust/extracted/pem --dest-cert-dir /etc/pki/ca-trust/extracted/pem {debug} {preserve_digests} {all_architectures} {src_options} {dest_options} {extra_arguments} docker://{src} docker://{dest}"#
                    .replace("{debug}", if debug { "--debug" } else { "" })
                    .replace("{ca_trust_bundle}", config.ca_trust_bundle.as_ref().map_or("", |s| s))
                    .replace("{preserve_digests}", if obj.spec.preserve_digests.unwrap_or(false) { "--preserve-digests" } else { "" })
                    .replace("{all_architectures}", if obj.spec.all_architectures.unwrap_or(false) { "--all" } else { "" })
                    .replace("{src_options}", if obj.spec.source.registry_login_secret.is_some() { "--src-authfile /creds/src/.dockerconfigjson" } else { "" })
                    .replace("{dest_options}", if obj.spec.destination.registry_login_secret.is_some() { "--dest-authfile /creds/dest/.dockerconfigjson" } else { "" })
                    .replace("{src}", &obj.spec.source.image)
                    .replace("{dest}", &obj.spec.destination.image)
                    .replace("{extra_arguments}", obj.spec.extra_skopeo_arguments.as_ref().map_or("", |s| s)));
    containers.push(Container {
        name: "skopeo".to_string(),
        image: Some(config.image.clone()),
        image_pull_policy: Some(config.image_pull_policy.clone()),
        command: Some(command),
        volume_mounts: Some(vec![
            VolumeMount {
                name: "creds-src".to_string(),
                mount_path: "/creds/src".to_string(),
                read_only: Some(true),
                ..Default::default()
            },
            VolumeMount {
                name: "creds-dest".to_string(),
                mount_path: "/creds/dest".to_string(),
                read_only: Some(true),
                ..Default::default()
            },
        ]),
        ..Default::default()
    });
    let jobtemplate = JobTemplateSpec {
        metadata: Some(ObjectMeta {
            labels: Some(BTreeMap::from([(
                "imagesync.apexnw.dev/imagesync-cron".to_string(),
                basename.clone(),
            )])),
            ..Default::default()
        }),
        spec: Some(JobSpec {
            template: PodTemplateSpec {
                metadata: Some(ObjectMeta {
                    labels: Some(BTreeMap::from([(
                        "imagesync.apexnw.dev/imagesync-cron".to_string(),
                        basename.clone(),
                    )])),
                    ..Default::default()
                }),
                spec: Some(PodSpec {
                    containers,
                    volumes: Some(vec![
                        Volume {
                            name: "creds-src".to_string(),
                            secret: obj.spec.source.registry_login_secret.as_ref().map(
                                |secret_name| SecretVolumeSource {
                                    secret_name: Some(secret_name.clone()),
                                    ..Default::default()
                                },
                            ),
                            ..Default::default()
                        },
                        Volume {
                            name: "creds-dest".to_string(),
                            secret: obj.spec.destination.registry_login_secret.as_ref().map(
                                |secret_name| SecretVolumeSource {
                                    secret_name: Some(secret_name.clone()),
                                    ..Default::default()
                                },
                            ),
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
    };
    CronJob {
        metadata: ObjectMeta {
            name: Some(format!("imagesync-{}", basename)),
            labels: Some(BTreeMap::from([(
                "imagesync.apexnw.dev/imagesync-cron".to_string(),
                basename.clone(),
            )])),
            ..Default::default()
        },
        spec: CronJobSpec {
            schedule: obj.spec.cron_schedule.as_ref().unwrap().to_string(),
            job_template: jobtemplate,
            ..Default::default()
        },
        ..Default::default()
    }
}

/// Create a CronJob for the given CR.
///
/// Because we can get all the information we need from the CR, we only pass the CR and the global SkopeoConfig to this function.
pub async fn create_cronjob(
    obj: &ImageSync,
    config: &SkopeoConfig,
    client: &Client,
) -> Result<(), Box<dyn std::error::Error>> {
    let debug = std::env::var("DEBUG").is_ok();
    let cronjobs: Api<CronJob> = Api::namespaced(
        client.clone(),
        obj.metadata.namespace.as_ref().unwrap().as_str(),
    );
    println!(
        "Creating job for imagesync: {}",
        obj.metadata.name.as_ref().unwrap()
    );
    let cronjob = generate_cronjob_object(obj, debug, config).await;
    cronjobs
        .create(&Default::default(), &cronjob)
        .await
        .unwrap();
    Ok(())
}

/// Return Time of last successful run of the CronJob, so we can update the status.
pub async fn cronjob_get_last_success(obj: ImageSync) -> Option<Time> {
    let cronjobs = get_cronjob_for_imagesync(&obj, &Client::try_default().await.unwrap())
        .await
        .unwrap();
    if let Some(cronjob) = cronjobs
        && let Some(status) = cronjob.status
        && let Some(last_success) = status.last_successful_time
    {
        return Some(last_success);
    }
    None
}

/// Checks if the given CronJob's spec matches the spec we would expect for a given ImageSync CR.
///
/// This function has to generate the expected spec as if we were going to create one, then compare it to the spec of the given Cronjob.
/// Returns true if the specs match, false otherwise.
pub async fn is_cronjob_spec_correct(
    obj: &ImageSync,
    cronjob: &CronJob,
    config: &SkopeoConfig,
) -> bool {
    let expected_cronjob =
        generate_cronjob_object(obj, std::env::var("DEBUG").is_ok(), config).await;
    if let expected_spec = expected_cronjob.spec
        && let actual_spec = &cronjob.spec
    {
        if !expected_spec.schedule.eq(&actual_spec.schedule) {
            println!(
                "CronJob schedule does not match expected: {} != {}",
                expected_spec.schedule, actual_spec.schedule
            );
            return false;
        }
        if let Some(expected_job_template) = expected_spec.job_template.spec
            && let Some(actual_job_template) = &actual_spec.job_template.spec
        {
            if expected_job_template.backoff_limit != actual_job_template.backoff_limit {
                println!(
                    "CronJob backoff_limit does not match expected: {:?} != {:?}",
                    expected_job_template.backoff_limit, actual_job_template.backoff_limit
                );
                return false;
            }
            if let Some(expected_pod_spec) = expected_job_template.template.spec
                && let Some(actual_pod_spec) = &actual_job_template.template.spec
            {
                
                let expected_src_cred_volume = expected_pod_spec.volumes.as_ref().unwrap().iter().find(|v| v.name == "creds-src");
                let actual_src_cred_volume = actual_pod_spec.volumes.as_ref().unwrap().iter().find(|v| v.name == "creds-src");
                let expected_dest_cred_volume = expected_pod_spec.volumes.as_ref().unwrap().iter().find(|v| v.name == "creds-dest");
                let actual_dest_cred_volume = actual_pod_spec.volumes.as_ref().unwrap().iter().find(|v| v.name == "creds-dest");
                // Compare the source and destination credential volumes, which are optional. We have to explicitly check the secret_name field of the SecretVolumeSource, because the cluster likes to fill in default values.
                let expected_src_secret_name = expected_src_cred_volume.and_then(|v| v.secret.as_ref().and_then(|s| s.secret_name.clone()));
                let actual_src_secret_name = actual_src_cred_volume.and_then(|v| v.secret.as_ref().and_then(|s| s.secret_name.clone()));
                let expected_dest_secret_name = expected_dest_cred_volume.and_then(|v| v.secret.as_ref().and_then(|s| s.secret_name.clone()));
                let actual_dest_secret_name = actual_dest_cred_volume.and_then(|v| v.secret.as_ref().and_then(|s| s.secret_name.clone()));
                if expected_src_secret_name != actual_src_secret_name {
                    println!(
                        "CronJob source credential secret name does not match expected: {:?} != {:?}",
                        expected_src_secret_name, actual_src_secret_name
                    );
                    return false;
                }
                if expected_dest_secret_name != actual_dest_secret_name {
                    println!(
                        "CronJob destination credential secret name does not match expected: {:?} != {:?}",
                        expected_dest_secret_name, actual_dest_secret_name
                    );
                    return false;
                }
                if !expected_pod_spec.containers[0].image.eq(&actual_pod_spec.containers[0].image) {
                    println!(
                        "CronJob container image does not match expected: {:?} != {:?}",
                        expected_pod_spec.containers[0].image, actual_pod_spec.containers[0].image
                    );
                    return false;
                }
                if !expected_pod_spec.containers[0].command.eq(&actual_pod_spec.containers[0].command) {
                    println!(
                        "CronJob container command does not match expected: {:?} != {:?}",
                        expected_pod_spec.containers[0].command, actual_pod_spec.containers[0].command
                    );
                    return false;
                }
                if !expected_pod_spec.containers[0].image_pull_policy.eq(&actual_pod_spec.containers[0].image_pull_policy) {
                    println!(
                        "CronJob container image_pull_policy does not match expected: {:?} != {:?}",
                        expected_pod_spec.containers[0].image_pull_policy, actual_pod_spec.containers[0].image_pull_policy
                    );
                    return false;
                }
                if !expected_pod_spec.containers[0].volume_mounts.eq(&actual_pod_spec.containers[0].volume_mounts) {
                    println!(
                        "CronJob container volume_mounts do not match expected: {:?} != {:?}",
                        expected_pod_spec.containers[0].volume_mounts, actual_pod_spec.containers[0].volume_mounts
                    );
                    return false;
                }
                return true;
            } else {
                println!("Failed to get pod spec from cronjob");
                return false;
            }
        } else {
            println!("Failued to get job template spec from cronjob");
            return false;
        }
    }
    false
}
