use prost::Message;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::process::{Child, Command, Stdio};

pub mod pb {
    include!(concat!(env!("OUT_DIR"), "/agent.pb.rs"));
}

use pb::*;

// ══════════════════════════════════════════════════
// 1. 传输层
// ══════════════════════════════════════════════════

struct ScalaProcess {
    child: Child,
}

impl ScalaProcess {
    fn spawn(jar_path: &str) -> Self {
        let java = std::env::var("JAVA_HOME")
            .map(|h| format!("{}/bin/java", h))
            .unwrap_or_else(|_| "java".to_string());

        let child = Command::new(&java)
            .args(["-jar", jar_path])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap_or_else(|e| panic!("Cannot spawn java: {}", e));

        let mut proc = ScalaProcess { child };

        let resp = proc.read_response();
        match resp.response {
            Some(response::Response::Ready(r)) =>
                eprintln!("  [rust] Scala ready (v{})", r.version),
            other => panic!("Expected ready, got: {:?}", other),
        }
        proc
    }

    fn send_receive(&mut self, req: Request) -> Response {
        self.write_request(&req);
        self.read_response()
    }

    fn write_request(&mut self, req: &Request) {
        let stdin = self.child.stdin.as_mut().unwrap();
        let buf = req.encode_to_vec();
        let len = (buf.len() as u32).to_be_bytes();
        stdin.write_all(&len).unwrap();
        stdin.write_all(&buf).unwrap();
        stdin.flush().unwrap();
    }

    fn read_response(&mut self) -> Response {
        let stdout = self.child.stdout.as_mut().unwrap();
        let mut len_buf = [0u8; 4];
        stdout.read_exact(&mut len_buf).unwrap();
        let len = u32::from_be_bytes(len_buf) as usize;
        let mut buf = vec![0u8; len];
        stdout.read_exact(&mut buf).unwrap();
        Response::decode(buf.as_slice()).unwrap()
    }
}

impl Drop for ScalaProcess {
    fn drop(&mut self) { let _ = self.child.kill(); }
}

// ══════════════════════════════════════════════════
// 2. Runtime
// ══════════════════════════════════════════════════

struct Runtime {
    scala: ScalaProcess,
    state: HashMap<String, String>,
}

impl Runtime {
    fn new(scala: ScalaProcess) -> Self {
        Runtime { scala, state: HashMap::new() }
    }

    fn run(&mut self, agent_type: &str, agent_id: &str, msg: &str) -> String {
        println!("  [rust] receive({}, {}, \"{}\")", agent_type, agent_id, msg);
        let req = Request {
            request: Some(request::Request::Receive(ReceiveRequest {
                agent_type: agent_type.into(),
                msg: msg.into(),
            })),
        };
        let mut resp = self.scala.send_receive(req);
        self.interpret_loop(agent_id, &mut resp)
    }

    fn interpret_loop(&mut self, agent_id: &str, resp: &mut Response) -> String {
        loop {
            match &resp.response {
                Some(response::Response::Done(d)) => {
                    // Streaming: 打印最后一轮的 emissions
                    for em in &d.emissions {
                        println!("  [stream] {}", em);
                    }
                    println!("  [rust] <- done: {}", d.result);
                    return d.result.clone();
                }
                Some(response::Response::Effect(eff)) => {
                    let cont = eff.cont.as_ref().unwrap();

                    // Streaming: 打印本轮 emissions
                    for em in &eff.emissions {
                        println!("  [stream] {}", em);
                    }

                    println!("  [rust]    step={} results={:?}", cont.resume_at, cont.results);

                    // 根据 kind 分发: 单个 / 并行
                    let effect_result = match &eff.kind {
                        Some(effect_response::Kind::Single(op)) => {
                            self.execute_with_retry(agent_id, op, cont)
                        }
                        Some(effect_response::Kind::Parallel(par)) => {
                            self.execute_parallel(agent_id, &par.ops, cont)
                        }
                        None => String::new(),
                    };

                    let req = Request {
                        request: Some(request::Request::Resume(ResumeRequest {
                            cont: Some(cont.clone()),
                            response: effect_result,
                        })),
                    };
                    *resp = self.scala.send_receive(req);
                }
                Some(response::Response::Ready(_)) => panic!("unexpected ready"),
                None => panic!("empty response"),
            }
        }
    }

    // ── Supervision: 带重试的 effect 执行 ──

    fn execute_with_retry(&mut self, agent_id: &str, op: &AgentOp, cont: &ContState) -> String {
        let max_retries = cont.policy.as_ref().map(|p| p.max_retries).unwrap_or(0);

        let mut attempts = 0;
        loop {
            match self.try_execute(agent_id, op) {
                Ok(result) => return result,
                Err(e) => {
                    attempts += 1;
                    if attempts > max_retries {
                        let on_exhaust = cont.policy.as_ref().map(|p| p.on_exhaust).unwrap_or(0);
                        return match on_exhaust {
                            0 => {
                                println!("  [supervision] STOP: retries exhausted");
                                format!("error:stop:{}", e)
                            }
                            1 => {
                                println!("  [supervision] ESCALATE: retries exhausted");
                                // 集群扩展: 通知上游 supervisor
                                format!("error:escalate:{}", e)
                            }
                            2 => {
                                println!("  [supervision] DEAD_LETTER: retries exhausted");
                                // 集群扩展: 发到死信队列
                                format!("error:dead_letter:{}", e)
                            }
                            _ => format!("error:{}", e),
                        };
                    }
                    println!("  [supervision] retry {}/{} ({})", attempts, max_retries, e);
                    // 集群扩展: 指数退避, circuit breaker
                }
            }
        }
    }

    fn try_execute(&mut self, agent_id: &str, op: &AgentOp) -> Result<String, String> {
        match &op.op {
            Some(agent_op::Op::Ask(a)) => {
                println!("  [rust]      ask {} \"{}\"", a.target, trunc(&a.payload, 50));

                // Supervision demo: unreliable_service 前 2 次失败
                if a.target == "unreliable_service" {
                    let fk = format!("{}:__fail:{}", agent_id, a.target);
                    let count = self.state.get(&fk).and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
                    self.state.insert(fk, (count + 1).to_string());
                    if count < 2 {
                        return Err(format!("service_unavailable (attempt {})", count + 1));
                    }
                }

                Ok(format!("[{} says: '{}']", a.target, trunc(&a.payload, 30)))
            }
            Some(agent_op::Op::Tell(t)) => {
                println!("  [rust]      tell {} \"{}\"", t.target, trunc(&t.payload, 50));
                Ok(String::new())
            }
            Some(agent_op::Op::LoadState(l)) => {
                let sk = format!("{}:{}", agent_id, l.key);
                let v = self.state.get(&sk).cloned().unwrap_or_default();
                println!("  [rust]      load \"{}\" = \"{}\"", l.key, trunc(&v, 40));
                Ok(v)
            }
            Some(agent_op::Op::SaveState(s)) => {
                let sk = format!("{}:{}", agent_id, s.key);
                println!("  [rust]      save \"{}\" = \"{}\"", s.key, trunc(&s.value, 40));
                self.state.insert(sk, s.value.clone());
                Ok(String::new())
            }
            None => Ok(String::new()),
        }
    }

    // ── Parallel: 顺序执行所有 ops，结果拼为 JSON 数组 ──
    // 集群扩展: 改为 tokio::spawn + join_all 真并发

    fn execute_parallel(&mut self, agent_id: &str, ops: &[AgentOp], cont: &ContState) -> String {
        println!("  [rust]    parallel: {} ops", ops.len());
        let results: Vec<String> = ops.iter().map(|op| {
            self.execute_with_retry(agent_id, op, cont)
        }).collect();

        // JSON 数组格式
        format!("[{}]", results.iter()
            .map(|r| format!("\"{}\"", r.replace('"', "\\\"")))
            .collect::<Vec<_>>()
            .join(","))
    }
}

fn trunc(s: &str, max: usize) -> &str {
    if s.len() <= max { s } else { &s[..max] }
}

// ══════════════════════════════════════════════════
// 3. Main
// ══════════════════════════════════════════════════

fn main() {
    let jar_path = std::env::args().nth(1).unwrap_or_else(|| {
        eprintln!("Usage: actor-runtime <agent-core.jar>");
        std::process::exit(1);
    });

    println!("=== Rust + Scala: Supervision + Streaming + Parallel ===\n");
    let scala = ScalaProcess::spawn(&jar_path);
    let mut rt = Runtime::new(scala);

    println!("--- Counter (backward compat) ---");
    for cmd in ["inc", "inc", "dec"] {
        let r = rt.run("counter", "c1", cmd);
        println!("  => {}\n", r);
    }

    println!("--- Assistant (streaming) ---");
    let r = rt.run("assistant", "a1", "Hello!");
    println!("  => {}\n", r);

    println!("--- Flaky (supervision: retry 3x) ---");
    let r = rt.run("flaky", "f1", "important request");
    println!("  => {}\n", r);

    println!("--- Researcher (parallel + streaming) ---");
    let r = rt.run("researcher", "r1", "quantum computing");
    println!("  => {}\n", r);

    println!("=== Done ===");
}
