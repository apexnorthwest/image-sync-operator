// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
/*
Utility functions for the reconciler loop.
*/

use crate::imagesync::{ImageSync, ImageSyncStatus};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::Time;
use kube::api::{Patch, PatchParams};
use kube::{Api, Client};
use regex::Regex;

/// Checks if the spec of the ImageSync CR has changed since the last time it was applied. Returns true if it has changed, false otherwise.
/// 
/// All this function does is convert the spec and status.last_applied_config to JSON and compare them. If they are equal, then the spec has not changed. If they are not equal, then the spec has changed.
pub async fn has_config_changed(obj: &ImageSync) -> Result<bool, Box<dyn std::error::Error>> {
    let spec_json = serde_json::to_value(obj.spec.clone())?;
    if let Some(status) = &obj.status
        && let Some(last_applied_config) = Some(status.clone().last_applied_config)
    {
        let last_applied_config_json = serde_json::to_value(last_applied_config.clone())?;
        if last_applied_config_json.eq(&spec_json) {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Take a list of new status values and update the CR. This is a common task in the reconciler loop so we abstract it out.
pub async fn update_status(
    obj: ImageSync,
    accepted: bool,
    ready: bool,
    accepted_message: String,
    last_completion_time: Option<Time>,
    client: &Client,
) -> Result<(), Box<dyn std::error::Error>> {
    println!(
        "ImageSync {} config has changed, setting ready=false",
        obj.metadata.name.clone().unwrap()
    );
    let imagesyncs: Api<ImageSync> = Api::namespaced(
        client.clone(),
        obj.metadata.namespace.clone().unwrap().as_str(),
    );
    let mut patched_obj = obj.clone();
    patched_obj.status = Some(ImageSyncStatus {
        accepted,
        message: accepted_message,
        ready,
        last_applied_config: obj.spec.clone(),
        last_completion_time,
    });
    let patch_params = PatchParams::apply("image-sync-operator").force();
    let patch = Patch::Apply(&patched_obj);
    match imagesyncs
        .patch_status(&obj.metadata.name.clone().unwrap(), &patch_params, &patch)
        .await
    {
        Ok(_) => {
            println!(
                "Successfully updated status for ImageSync {}",
                obj.metadata.name.clone().unwrap()
            );
            Ok(())
        }
        Err(e) => {
            println!(
                "Failed to update status for ImageSync {}: {}",
                obj.metadata.name.clone().unwrap(),
                e
            );
            Err(Box::new(e))
        }
    }
}

/// When the ImageSync spec has changed, we need to reset the status to not accepted.
/// 
/// This function simply calls the update_status function with the appropriate values to reset the status to not accepted.
pub async fn reset_to_not_accepted(
    obj: &ImageSync,
    client: &Client,
) -> Result<(), Box<dyn std::error::Error>> {
    println!(
        "Resetting ImageSync {} to not accepted",
        obj.metadata.name.clone().unwrap()
    );
    update_status(
        obj.clone(),
        false,
        false,
        String::from("ImageSync configuration has changed"),
        None,
        client,
    )
    .await?;
    Ok(())
}

/// Run acceptance checks on the ImageSync spec.
/// 
/// Whenever the spec changes, we need to verify that it's valid and that the secrets exist.
/// This function returns true if the spec is valid and false otherwise.
/// It also calls the update_status function to update the status of the ImageSync CR with the results of the acceptance checks.
pub async fn acceptance_checks(
    obj: &ImageSync,
    client: &Client,
) -> Result<bool, Box<dyn std::error::Error>> {
    let mut source_secret_okay = true;
    let mut source_secret_message = String::new();
    let mut dest_secret_okay = true;
    let mut dest_secret_message = String::new();
    let mut cron_schedule_okay = true;
    let mut source_image_okay = true;
    let mut dest_image_okay = true;

    let secrets = Api::<k8s_openapi::api::core::v1::Secret>::namespaced(
        client.clone(),
        obj.metadata.namespace.as_ref().unwrap().as_str(),
    );

    // Acceptance check for the source secret
    if let Some(secret_name) = &obj.spec.source.registry_login_secret {
        match secrets.get(secret_name).await {
            Ok(secret) => {
                println!("Source secret {} exists", secret_name);
                if secret.type_ != Some(String::from("kubernetes.io/dockerconfigjson")) {
                    println!(
                        "Source secret {} is not of type kubernetes.io/dockerconfigjson",
                        secret_name
                    );
                    source_secret_okay = false;
                    source_secret_message = format!(
                        "Source secret {} is not of type kubernetes.io/dockerconfigjson",
                        secret_name
                    );
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
    if let Some(secret_name) = &obj.spec.destination.registry_login_secret {
        match secrets.get(secret_name).await {
            Ok(secret) => {
                println!("Destination secret {} exists", secret_name);
                if secret.type_ != Some(String::from("kubernetes.io/dockerconfigjson")) {
                    println!(
                        "Destination secret {} is not of type kubernetes.io/dockerconfigjson",
                        secret_name
                    );
                    dest_secret_okay = false;
                    dest_secret_message = format!(
                        "Destination secret {} is not of type kubernetes.io/dockerconfigjson",
                        secret_name
                    );
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
    if let Some(schedule) = &obj.spec.cron_schedule {
        // This regex is a bit of a guess since the official k8s api spec doesn't define the exact format. We presume it's the same as most other cron implementions.
        if !Regex::new(r"^((((\d+,)+\d+|(\d+(/|-|#)\d+)|\d+L?|\*(/\d+)?|L(-\d+)?|\?|[A-Z]{3}(-[A-Z]{3})?) ?){5,7})$|(@(annually|yearly|monthly|weekly|daily|hourly|reboot))|(@every (\d+(ns|us|µs|ms|s|m|h))+)$").unwrap().is_match(schedule) {
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
    if !source_secret_okay
        || !dest_secret_okay
        || !cron_schedule_okay
        || !source_image_okay
        || !dest_image_okay
    {
        accepted = false;
        accepted_message = String::from("ImageSync configuration is invalid: ");
        if !source_secret_okay {
            accepted_message.push_str(&format!("Source secret error: {}. ", source_secret_message));
        }
        if !dest_secret_okay {
            accepted_message.push_str(&format!(
                "Destination secret error: {}. ",
                dest_secret_message
            ));
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

    update_status(obj.clone(), accepted, false, accepted_message, None, client).await?;
    Ok(accepted)
}
