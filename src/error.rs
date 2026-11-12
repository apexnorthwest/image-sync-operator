// Copyright 2026 Apex Northwest
// SPDX-License-Identifier: Apache-2.0
//! This module defines a common error type for the ImageSync operator.


/// Catchall error type for the operator. Because our many functions can throw many types of errors
/// we don't want to use dyn error types. We are mostly just logging and retrying on all of them, so
/// we wrap their string representations in this type. `From` implementations are provided to ease 
/// conversion from common error types.
#[derive(thiserror::Error)]
pub enum OpError {
    StringError(String),
}

pub type Result<T, E = OpError> = std::result::Result<T, E>;

impl From<String> for OpError {
    fn from(s: String) -> Self {
        OpError::StringError(s)
    }
}

impl std::fmt::Display for OpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OpError::StringError(s) => write!(f, "{}", s),
        }
    }
}

impl std::fmt::Debug for OpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OpError::StringError(s) => write!(f, "{}", s),
        }
    }
}

impl From<serde_json::Error> for OpError {
    fn from(e: serde_json::Error) -> Self {
        OpError::StringError(e.to_string())
    }
}

impl From<kube::Error> for OpError {
    fn from(e: kube::Error) -> Self {
        OpError::StringError(e.to_string())
    }
}
