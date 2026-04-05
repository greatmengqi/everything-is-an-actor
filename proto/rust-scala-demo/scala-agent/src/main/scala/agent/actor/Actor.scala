package agent.actor

import agent.{AgentOp, Ctx}
import agent.AgentOp.*

// ══════════════════════════════════════════════════════════════
// Actor API — 基于 effect 系统的类型安全抽象
//
// 用户视角:
//   class MyActor extends Actor:
//     def receive(ctx: ActorContext, msg: String): String = ...
//
// 底层:
//   ref.ask(msg)   → ctx.effect(Ask(ref.id, msg))
//   ref.tell(msg)  → ctx.effect(Tell(ref.id, msg))
//   ctx.spawn(...)  → ctx.effect(Ask("__system__", "spawn:type:id"))
//
// Actor API 是纯 Scala 类型体操，Rust 侧零改动。
// ══════════════════════════════════════════════════════════════

// ─────────────────────────────────────────────
// ActorRef — 类型安全的 actor 引用
// ─────────────────────────────────────────────

/** 指向一个 actor 的引用，只能发送它接受的消息类型 */
class ActorRef[M](val id: String):
  /** 发消息，不等回复 */
  def tell(msg: M)(using ctx: ActorContext): Unit =
    ctx.raw.effect(Tell(id, msg.toString))

  /** 发消息，等回复 */
  def ask(msg: M)(using ctx: ActorContext): String =
    ctx.raw.effect(Ask(id, msg.toString))

  override def toString: String = s"ActorRef($id)"

// ─────────────────────────────────────────────
// ActorContext — actor 内部可用的上下文
// ─────────────────────────────────────────────

/** 包装 Ctx，暴露 actor 友好的 API */
class ActorContext private[actor] (private[actor] val raw: Ctx, val self: ActorRef[?]):

  /** 获取另一个 actor 的引用 */
  def actorRef[M](id: String): ActorRef[M] = ActorRef[M](id)

  /** 状态读取 */
  def load(key: String): String = raw.effect(LoadState(key))

  /** 状态写入 */
  def save(key: String, value: String): Unit = raw.effect(SaveState(key, value))

  /** 流式输出 */
  def emit(item: String): Unit = raw.emit(item)

  /** 并行调用多个 actor */
  def askAll(refs: (ActorRef[?], String)*): String =
    raw.parallel(refs.map((ref, msg) => Ask(ref.id, msg.toString))*)

  /** 声明容错策略 */
  def withPolicy(maxRetries: Int, onExhaust: agent.pb.agent.OnExhaust = agent.pb.agent.OnExhaust.STOP): Unit =
    raw.withPolicy(maxRetries, onExhaust)

// ─────────────────────────────────────────────
// Actor trait — 用户继承这个
// ─────────────────────────────────────────────

/** 定义一个 actor 的行为 */
trait Actor:
  /** 处理消息，返回结果 */
  def receive(msg: String)(using ctx: ActorContext): String

// ─────────────────────────────────────────────
// ActorSystem — actor 注册表 + 桥接到 Interpreter
// ─────────────────────────────────────────────

object ActorSystem:
  private var actors = Map.empty[String, Actor]

  /** 注册一个 actor 类型 */
  def register(agentType: String, actor: Actor): Unit =
    actors = actors + (agentType -> actor)

  /** 供 Interpreter 调用 — 把 actor.receive 适配为 (Ctx, String) => String */
  def run(agentType: String, ctx: Ctx, msg: String): Option[String] =
    actors.get(agentType).map { actor =>
      given actorCtx: ActorContext = ActorContext(ctx, ActorRef(agentType))
      actor.receive(msg)
    }
