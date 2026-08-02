// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
/*
Job management for ImageSync operator.
This only controls the one-shot and initial jobs.
*/

use crate::config::SkopeoConfig;
use crate::imagesync::ImageSync;
use k8s_openapi::api::batch::v1::{Job, JobSpec};
use k8s_openapi::api::core::v1::{
    Container, PodSpec, PodTemplateSpec, SecretVolumeSource, Volume, VolumeMount,
};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta;
use kube::{Api, Client};
use std::collections::BTreeMap;

// Delete the given job.
pub async fn delete_job(job: &Job, client: &Client) -> Result<(), Box<dyn std::error::Error>> {
    let jobs: Api<Job> = Api::namespaced(client.clone(), job.metadata.namespace.as_ref().unwrap());
    match jobs
        .delete(
            job.metadata.name.clone().unwrap().as_str(),
            &Default::default(),
        )
        .await
    {
        Ok(_) => {
            println!("Deleted job {}", job.metadata.name.clone().unwrap());
            Ok(())
        }
        Err(e) => {
            println!(
                "Failed to delete job {}: {}",
                job.metadata.name.as_ref().unwrap(),
                e
            );
            Err(Box::new(e))
        }
    }
}

// Get the job associated with the given ImageSync CR, if it exists.
pub async fn get_job_for_imagesync(
    obj: &ImageSync,
    client: &Client,
) -> Result<Option<Job>, Box<dyn std::error::Error>> {
    let jobs: Api<Job> = Api::namespaced(
        client.clone(),
        obj.metadata.namespace.as_ref().unwrap().as_str(),
    );
    let basename = if obj.metadata.name.as_ref().unwrap().len() > 50 {
        obj.metadata.name.as_ref().unwrap()[0..50].to_string()
    } else {
        obj.metadata.name.as_ref().unwrap().to_string()
    };
    let joblist = jobs
        .list(
            &kube::api::ListParams::default()
                .labels(&format!("imagesync.apexnw.dev/imagesync={}", basename)),
        )
        .await?;
    if joblist.items.len() == 1 {
        Ok(Some(joblist.items[0].clone()))
    } else if joblist.items.len() > 1 {
        eprintln!(
            "Found multiple jobs for ImageSync {}. This should not happen.",
            basename
        );
        Err(Box::new(std::io::Error::other(
            "Multiple jobs found for ImageSync",
        )))
    } else {
        Ok(None)
    }
}

// Create a one-shot sync Job for the given CR.
pub async fn create_job(
    obj: &ImageSync,
    config: &SkopeoConfig,
    client: &Client,
) -> Result<(), Box<dyn std::error::Error>> {
    let jobs: Api<Job> = Api::namespaced(
        client.clone(),
        obj.metadata.namespace.as_ref().unwrap().as_str(),
    );
    let basename = if obj.metadata.name.as_ref().unwrap().len() > 50 {
        obj.metadata.name.as_ref().unwrap()[0..50].to_string()
    } else {
        obj.metadata.name.as_ref().unwrap().to_string()
    };
    println!(
        "Creating job for imagesync: {}",
        obj.metadata.name.as_ref().unwrap()
    );
    let mut containers = Vec::<Container>::new();
    let mut command: Vec<String> = Vec::<String>::new();
    command.push("/bin/bash".to_string());
    command.push("-c".to_string());
    command.push(r#"cat >> /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem <<EOF
{ca_trust_bundle}
EOF
skopeo copy {preserve_digests} {all_architectures} {src_options} {dest_options} {extra_arguments} docker://{src} docker://{dest}"#
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
    let job = Job {
        metadata: ObjectMeta {
            name: Some(format!("imagesync-{}", basename)),
            labels: Some(BTreeMap::from([(
                "imagesync.apexnw.dev/imagesync".to_string(),
                basename.clone(),
            )])),
            ..Default::default()
        },
        spec: Some(JobSpec {
            template: PodTemplateSpec {
                metadata: Some(ObjectMeta {
                    labels: Some(BTreeMap::from([(
                        "imagesync.apexnw.dev/imagesync".to_string(),
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
        status: None,
    };
    jobs.create(&Default::default(), &job).await.unwrap();
    Ok(())
}

// Check if the given job has finished running or has failed.
pub async fn is_job_complete(job: &Job) -> bool {
    if let Some(status) = &job.status
        && let Some(conditions) = &status.conditions
    {
        for condition in conditions {
            if condition.type_ == "Complete" && condition.status == "True" {
                return true;
            }
        }
    }
    false
}

// Check if the job has failed.
pub async fn is_job_failed(job: &Job) -> bool {
    if let Some(status) = &job.status
        && let Some(conditions) = &status.conditions
    {
        for condition in conditions {
            if condition.type_ == "Failed" && condition.status == "True" {
                return true;
            }
        }
    }
    false
}
