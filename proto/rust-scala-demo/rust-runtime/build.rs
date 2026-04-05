fn main() {
    prost_build::compile_protos(&["../proto/agent.proto"], &["../proto/"]).unwrap();
}
