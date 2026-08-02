// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
//! Config represents the yaml config file consumed by the operator at startup from /config/config.yaml.
//! The main loop uses the read_config_file() function to read the config file and return a Config struct.

use serde::Deserialize;
use std::fs;

/// The Config struct represents the configuration of the operator as passed via the config.yaml file. This is mounted from a ConfigMap.
#[derive(Debug, Deserialize)]
pub struct Config {
    pub cluster_scope: bool,
    pub watched_namespaces: Vec<String>,
    pub skopeo: SkopeoConfig,
}

/// The SkopeoConfig struct represents the section of the Config that configures the global settings of the Skopeo Job containers.
#[derive(Debug, Deserialize)]
pub struct SkopeoConfig {
    pub image: String,
    pub image_pull_policy: String,
    pub ca_trust_bundle: Option<String>,
}

/// Read the config file from /config/config.yaml and return a Config struct. This is used lazily by the main loop to read the config at startup.
pub fn read_config_file() -> Result<Config, Box<dyn std::error::Error>> {
    let config_file_path = "/config/config.yaml";
    let config_content = fs::read_to_string(config_file_path)?;
    let config: Config = yaml_serde::from_str(&config_content)?;
    Ok(config)
}
