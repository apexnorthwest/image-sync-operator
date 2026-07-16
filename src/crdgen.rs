mod imagesync;
use imagesync::ImageSync;
use kube::CustomResourceExt;
use yaml_serde;

fn main() {
    print!("{}", yaml_serde::to_string(&ImageSync::crd()).unwrap());
}