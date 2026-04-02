//! Rust-native Actor core for Python with async handler support
//!
//! Key insight: Rust mailbox is fast (4.5M msg/s) but PyO3 bridge adds overhead.
//! This module provides the core Rust mailbox for benchmarking.

use crossbeam_channel::{bounded, Receiver as CbReceiver, Sender as CbSender};
use parking_lot::RwLock;
use pyo3::prelude::*;
use std::sync::Arc;
use std::thread;
use uuid::Uuid;

// Type aliases
type ActorMap = std::collections::HashMap<String, Arc<RustActorInner>>;

/// Message envelope for actor communication
pub struct Envelope {
    pub id: String,
    pub sender: Option<String>,
    pub message: Py<PyAny>,
    pub reply_tx: Option<CbSender<Py<PyAny>>>,
}

/// Internal Rust Actor with message processing loop
pub struct RustActorInner {
    path: String,
    sender: CbSender<Envelope>,
    state: Arc<RwLock<bool>>,
}

impl RustActorInner {
    fn new(path: String, capacity: usize) -> (Arc<Self>, CbReceiver<Envelope>) {
        let (tx, rx) = bounded(capacity);
        let actor = Arc::new(Self {
            path,
            sender: tx,
            state: Arc::new(RwLock::new(true)),
        });
        (actor, rx)
    }

    fn is_alive(&self) -> bool {
        *self.state.read()
    }

    fn stop(&self) {
        *self.state.write() = false;
    }

    fn get_sender(&self) -> CbSender<Envelope> {
        self.sender.clone()
    }
}

/// Rust Actor System - manages actors and message routing
#[pyclass(module = "rust_core")]
pub struct RustSystem {
    actors: Arc<RwLock<ActorMap>>,
}

#[pymethods]
impl RustSystem {
    #[new]
    fn new() -> Self {
        Self {
            actors: Arc::new(RwLock::new(ActorMap::new())),
        }
    }

    /// Spawn a Rust actor with a Python handler
    /// The handler is called in the Rust thread loop for each message
    #[allow(clippy::too_many_arguments)]
    fn spawn(
        &self,
        py: Python,
        path: String,
        capacity: usize,
        handler: PyObject,
    ) -> PyResult<String> {
        let (actor, receiver) = RustActorInner::new(path.clone(), capacity);

        // Store the actor
        self.actors.write().insert(path.clone(), actor.clone());

        // Spawn Rust thread for actor loop
        let actors_clone = self.actors.clone();
        let path_clone = path.clone();

        // Convert handler to a Py<PyAny> for the thread
        let handler_py: Py<PyAny> = handler.into();

        thread::spawn(move || {
            while actor.is_alive() {
                // Blocking receive with timeout
                match receiver.recv_timeout(std::time::Duration::from_millis(100)) {
                    Ok(envelope) => {
                        // Call Python handler via PyO3 unsafe API
                        Python::with_gil(|py| {
                            // Get handler as PyAny via pointer cast
                            let handler_ptr = handler_py.as_ptr();

                            // Build args tuple using FFI
                            let msg_ptr = envelope.message.as_ptr();
                            let args_tuple = unsafe { pyo3::ffi::PyTuple_New(1) };
                            if !args_tuple.is_null() {
                                unsafe {
                                    pyo3::ffi::PyTuple_SET_ITEM(
                                        args_tuple,
                                        0,
                                        msg_ptr as *mut pyo3::ffi::PyObject,
                                    );
                                    // Call the handler
                                    let result =
                                        pyo3::ffi::PyObject_CallObject(handler_ptr, args_tuple);
                                    if result.is_null() {
                                        // Error occurred, print it
                                        if !pyo3::ffi::PyErr_Occurred().is_null() {
                                            pyo3::ffi::PyErr_Print();
                                        }
                                    }
                                }
                            }
                        });
                    }
                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                    Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                }
            }
            // Remove from actors when done
            actors_clone.write().remove(&path_clone);
        });

        Ok(path)
    }

    /// Send a message to an actor
    fn tell(&self, path: String, message: Py<PyAny>) -> PyResult<bool> {
        match self.actors.read().get(&path) {
            Some(actor) => {
                let envelope = Envelope {
                    id: Uuid::new_v4().to_string(),
                    sender: None,
                    message,
                    reply_tx: None,
                };
                let _ = actor.get_sender().send(envelope);
                Ok(true)
            }
            None => Ok(false),
        }
    }

    /// Send a message and wait for reply
    fn ask(
        &self,
        py: Python,
        path: String,
        message: Py<PyAny>,
        timeout_ms: u64,
    ) -> PyResult<Py<PyAny>> {
        let (reply_tx, reply_rx) = bounded(1);

        match self.actors.read().get(&path) {
            Some(actor) => {
                let envelope = Envelope {
                    id: Uuid::new_v4().to_string(),
                    sender: None,
                    message,
                    reply_tx: Some(reply_tx),
                };
                let _ = actor.get_sender().send(envelope);

                // Wait for reply with timeout
                let timeout = std::time::Duration::from_millis(timeout_ms);
                match reply_rx.recv_timeout(timeout) {
                    Ok(reply) => Ok(reply),
                    Err(_) => Err(pyo3::exceptions::PyTimeoutError::new_err("timeout")),
                }
            }
            None => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Actor not found: {}",
                path
            ))),
        }
    }

    fn is_alive(&self, path: String) -> bool {
        self.actors
            .read()
            .get(&path)
            .map(|a| a.is_alive())
            .unwrap_or(false)
    }

    fn stop_actor(&self, path: String) -> bool {
        self.actors
            .read()
            .get(&path)
            .map(|a| {
                a.stop();
                true
            })
            .unwrap_or(false)
    }
}

impl Default for RustSystem {
    fn default() -> Self {
        Self::new()
    }
}

// ============================================================================
// Benchmarks
// ============================================================================

#[pyfunction]
fn bench_rust_actor_tell(count: usize) -> f64 {
    use std::time::Instant;

    let system = RustSystem::new();
    let path = "/bench/actor1";

    // Spawn actor with no-op handler
    Python::with_gil(|py| {
        system
            .spawn(py, path.to_string(), 100000, py.None())
            .unwrap();
    });

    let start = Instant::now();
    for i in 0..count {
        let msg = format!("message {}", i);
        Python::with_gil(|py| {
            system.tell(path.to_string(), msg.into_py(py)).unwrap();
        });
    }
    drop(system);

    let elapsed = start.elapsed();
    let throughput = count as f64 / elapsed.as_secs_f64();

    println!(
        "Rust actor tell: {} msg in {:.3}s ({:.0} msg/s)",
        count,
        elapsed.as_secs_f64(),
        throughput
    );

    throughput
}

#[pyfunction]
fn bench_rust_mailbox_throughput(count: usize) -> f64 {
    use std::time::Instant;

    let (tx, rx) = bounded(100000);

    thread::spawn(move || while rx.recv().is_ok() {});

    let start = Instant::now();
    for i in 0..count {
        let msg = format!("message {}", i);
        let _ = tx.send(msg);
    }
    drop(tx);

    let elapsed = start.elapsed();
    let throughput = count as f64 / elapsed.as_secs_f64();

    println!(
        "Rust mailbox: {} msg in {:.3}s ({:.0} msg/s)",
        count,
        elapsed.as_secs_f64(),
        throughput
    );

    throughput
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_envelope_creation() {
        let envelope = Envelope {
            id: "test-id".to_string(),
            sender: Some("sender".to_string()),
            message: pyo3::Python::with_gil(|py| py.None().into_py(py)),
            reply_tx: None,
        };
        assert_eq!(envelope.id, "test-id");
        assert_eq!(envelope.sender, Some("sender".to_string()));
    }

    #[test]
    fn test_actor_inner_creation() {
        let (actor, receiver) = RustActorInner::new("/test/actor".to_string(), 100);
        assert!(actor.is_alive());
        assert!(!receiver.is_empty());
    }

    #[test]
    fn test_actor_stop() {
        let (actor, _receiver) = RustActorInner::new("/test/actor".to_string(), 100);
        assert!(actor.is_alive());
        actor.stop();
        assert!(!actor.is_alive());
    }

    #[test]
    fn test_sender_clone() {
        let (actor, _receiver) = RustActorInner::new("/test/actor".to_string(), 100);
        let _sender1 = actor.get_sender();
        let _sender2 = actor.get_sender();
        // Both senders should be valid Arc references
    }
}

#[cfg(test)]
mod benchmark_tests {
    use super::*;

    #[test]
    fn test_mailbox_throughput_small() {
        let (tx, rx) = bounded(1000);
        thread::spawn(move || while rx.recv().is_ok() {});
        for i in 0..100 {
            let msg = format!("message {}", i);
            let _ = tx.send(msg);
        }
        drop(tx);
        // Test completes without deadlock
    }
}

#[pymodule]
pub fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustSystem>()?;
    m.add_function(pyo3::wrap_pyfunction!(bench_rust_actor_tell, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(bench_rust_mailbox_throughput, m)?)?;
    Ok(())
}
