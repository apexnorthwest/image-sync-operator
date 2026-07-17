use kube::CustomResource;
use serde::{Deserialize, Serialize};
use schemars::JsonSchema;
use chrono::{DateTime, Utc};
use chrono::serde::ts_seconds_option;

#[allow(non_snake_case)]
#[derive(CustomResource, Deserialize, Serialize, Clone, Debug, JsonSchema)]
#[kube(kind = "ImageSync", group = "imagesync.apexnw.dev", version = "v1alpha1", namespaced)]
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
}


#[derive(Deserialize, Serialize, Clone, Debug, JsonSchema)]
pub struct ImageSyncTarget {
    pub image: String,
    #[serde(rename = "registryLoginSecret", skip_serializing_if = "Option::is_none")]
    pub registry_login_secret: Option<String>
}

#[allow(non_snake_case)]
#[derive(Deserialize, Serialize, Clone, Debug, JsonSchema)]
pub struct ImageSyncStatus {
    pub accepted: bool,
    pub ready: bool,
    #[serde(rename = "lastAppliedConfig")]
    pub last_applied_config: ImageSyncSpec,
    #[serde(rename = "lastCompletionTime", serialize_with = "ts_seconds_option::serialize", deserialize_with = "ts_seconds_option::deserialize")]
    pub last_completion_time: Option<DateTime<Utc>>,
    pub message: String,
}
