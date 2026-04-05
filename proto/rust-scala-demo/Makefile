SCALA_DIR = scala-agent
RUST_DIR  = rust-runtime
LIB_EXT   = $(if $(filter Darwin,$(shell uname)),dylib,so)

# ── Quick: mock 模式，不需要 GraalVM ──
.PHONY: mock
mock:
	cd $(RUST_DIR) && cargo run -- --mock

# ── Full: Scala .so + Rust runtime ──
.PHONY: all
all: scala rust
	cp $(SCALA_DIR)/target/libagent.$(LIB_EXT) $(RUST_DIR)/
	cd $(RUST_DIR) && cargo run -- ./libagent.$(LIB_EXT)

.PHONY: scala
scala:
	cd $(SCALA_DIR) && sbt assembly
	native-image --shared \
		-jar $(SCALA_DIR)/target/scala-3.3.3/agent-core.jar \
		-o $(SCALA_DIR)/target/libagent \
		--no-fallback \
		-H:Name=libagent

.PHONY: rust
rust:
	cd $(RUST_DIR) && cargo build

.PHONY: clean
clean:
	cd $(SCALA_DIR) && sbt clean
	cd $(RUST_DIR) && cargo clean
