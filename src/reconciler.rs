// Utility functions for the reconciler loop

use crate::imagesync::{ImageSync, ImageSyncStatus};
use kube::{Api, Client};
use kube::api::{Patch, PatchParams};
use regex::Regex;

pub async fn acceptance_checks(obj: &ImageSync, secrets: &Api<k8s_openapi::api::core::v1::Secret>) -> Result<(bool, bool, String), Box<dyn std::error::Error>> {
    let mut source_secret_okay = true;
    let mut source_secret_message = String::new();
    let mut dest_secret_okay = true;
    let mut dest_secret_message = String::new();
    let mut cron_schedule_okay = true;
    let mut source_image_okay = true;
    let mut dest_image_okay = true;
    let mut fast_requeue = false;

    // Acceptance check for the source secret
    if let Some(secret_name) = &obj.spec.source.registry_login_secret {
        match secrets.get(secret_name).await {
            Ok(secret) => {
                println!("Source secret {} exists", secret_name);
                if secret.type_ != Some(String::from("kubernetes.io/dockerconfigjson")) {
                    println!("Source secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                    source_secret_okay = false;
                    source_secret_message = format!("Source secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                    fast_requeue = true;
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
                    println!("Destination secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                    dest_secret_okay = false;
                    dest_secret_message = format!("Destination secret {} is not of type kubernetes.io/dockerconfigjson", secret_name);
                    fast_requeue = true;
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
    return Ok((accepted, fast_requeue, accepted_message));
}

pub async fn update_status(obj: ImageSync, accepted: bool, accepted_message: String, client: &Client) -> Result<(), Box<dyn std::error::Error>> {
    println!("ImageSync {} config has changed, setting ready=false", obj.name_any());
    let imagesyncs: Api<ImageSync> = Api::namespaced(client.clone(), &obj.namespace().unwrap());
    let mut patched_obj = obj.clone();
    patched_obj.status = Some(ImageSyncStatus {
        accepted,
        message: accepted_message,
        ready: false,
        last_applied_config: obj.spec.clone(),
        last_completion_time: None,
    });
    let patch_params = PatchParams::apply("image-sync-operator").force();
    let patch = Patch::Apply(&patched_obj);
    match imagesyncs.patch_status(&obj.name_any(), &patch_params, &patch).await {
        Ok(_) => {
            println!("Successfully updated status for ImageSync {}", obj.name_any());
            Ok(())
        }
        Err(e) => {
            println!("Failed to update status for ImageSync {}: {}", obj.name_any(), e);
            Err(Box::new(e))
        }
    }
}
