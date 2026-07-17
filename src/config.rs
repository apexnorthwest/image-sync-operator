/*
Config represents the yaml config file consumed by the operator at startup from /config/config.yaml.
Use the read_config_file() function to read the config file and return a Config struct.
*/
use serde::Deserialize;
use yaml_serde;
use std::fs;

#[derive(Debug, Deserialize)]
pub struct Config {
    pub cluster_scope: bool,
    pub watched_namespaces: Vec<String>,
    pub skopeo: SkopeoConfig,
}

#[derive(Debug, Deserialize)]
pub struct SkopeoConfig {
    pub image: String,
    pub image_pull_policy: String,
    pub ca_trust_bundle: Option<String>,
}

pub fn read_config_file() -> Result<Config, Box<dyn std::error::Error>> {
    let config_file_path = "/config/config.yaml";
    let config_content = fs::read_to_string(config_file_path)?;
    let config: Config = yaml_serde::from_str(&config_content)?;
    Ok(config)
}