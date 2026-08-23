// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
//! This module contains the ImageSync object type and all configuration required to make serde behave.

use k8s_openapi::apimachinery::pkg::apis::meta::v1::Time;
use kube::CustomResource;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// The ImageSyncSpec is the in-code representation of the `spec` section of the ImageSync CR.
#[allow(non_snake_case)]
#[derive(CustomResource, Deserialize, Serialize, Clone, Debug, JsonSchema)]
#[kube(
    kind = "ImageSync",
    group = "imagesync.apexnw.dev",
    version = "v1alpha1",
    namespaced
)]
#[kube(status = "ImageSyncStatus")]
pub struct ImageSyncSpec {
    pub source: ImageSyncTarget,
    pub destination: ImageSyncTarget,
    #[serde(rename = "cronSchedule", skip_serializing_if = "Option::is_none")]
    pub cron_schedule: Option<String>,
    #[serde(rename = "allArchitectures", skip_serializing_if = "Option::is_none")]
    pub all_architectures: Option<bool>,
    #[serde(rename = "preserveDigests", skip_serializing_if = "Option::is_none")]
    pub preserve_digests: Option<bool>,
    #[serde(
        rename = "extraSkopeoArguments",
        skip_serializing_if = "Option::is_none"
    )]
    pub extra_skopeo_arguments: Option<String>,
}

/// The ImageSyncTarget is the in-code representation of the `source` and `destination` sections of the ImageSync CR's `spec`.
#[derive(Deserialize, Serialize, Clone, Debug, JsonSchema)]
pub struct ImageSyncTarget {
    pub image: String,
    #[serde(
        rename = "registryLoginSecret",
        skip_serializing_if = "Option::is_none"
    )]
    pub registry_login_secret: Option<String>,
}

/// The ImageSyncStatus is the in-code representation of the `status` section of the ImageSync CR.
#[allow(non_snake_case)]
#[derive(Deserialize, Serialize, Clone, Debug, JsonSchema)]
pub struct ImageSyncStatus {
    pub accepted: bool,
    pub ready: bool,
    #[serde(rename = "lastAppliedConfig")]
    pub last_applied_config: String,
    #[serde(rename = "lastCompletionTime")]
    pub last_completion_time: Option<Time>,
    pub message: String,
}
