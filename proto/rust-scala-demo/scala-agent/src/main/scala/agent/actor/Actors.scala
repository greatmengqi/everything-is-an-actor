package agent.actor

// ══════════════════════════════════════════════════════════════
// 用 Actor API 写的示例
//
// 对比 raw Ctx 写法:
//   def assistant(ctx: Ctx, msg: String): String =
//     val history = ctx.effect(LoadState("chat_history"))
//     val response = ctx.effect(Ask("llm", prompt))
//
// Actor 写法:
//   class AssistantActor extends Actor:
//     def receive(msg: String)(using ctx: ActorContext): String =
//       val history = ctx.load("chat_history")
//       val response = llm.ask(prompt)
//
// 区别: ActorRef 类型安全 + 语义清晰
// ══════════════════════════════════════════════════════════════

// ── LLM actor: 包装 LLM 调用 ──

object LlmActor extends Actor:
  def receive(msg: String)(using ctx: ActorContext): String =
    // 这里可以加 prompt engineering、token 计数等逻辑
    ctx.emit(s"[LLM] Processing: ${msg.take(50)}...")
    // 实际调用走 effect，Rust 侧对接真实 LLM API
    ctx.raw.effect(agent.AgentOp.Ask("llm_api", msg))

// ── 翻译 actor ──

object TranslatorActor extends Actor:
  private val llm = ActorRef[String]("llm_actor")

  def receive(msg: String)(using ctx: ActorContext): String =
    llm.ask(s"Translate to English: $msg")

// ── 聊天助手 actor ──

object ChatActor extends Actor:
  private val llm    = ActorRef[String]("llm_actor")
  private val audit  = ActorRef[String]("audit_actor")

  def receive(msg: String)(using ctx: ActorContext): String =
    val history = ctx.load("chat_history")
    val prompt  = if history.isEmpty then s"User: $msg"
                  else s"$history\nUser: $msg"

    ctx.emit(s"Thinking about: $msg")
    val response = llm.ask(prompt)

    ctx.emit("Saving conversation...")
    ctx.save("chat_history", s"$prompt\nAssistant: $response")
    audit.tell(s"processed:$msg")

    response

// ── 审计日志 actor ──

object AuditActor extends Actor:
  def receive(msg: String)(using ctx: ActorContext): String =
    val log = ctx.load("audit_log")
    ctx.save("audit_log", s"$log\n$msg")
    "logged"

// ── 研究员 actor: parallel + streaming ──

object ResearcherActor extends Actor:
  private val sourceA = ActorRef[String]("source_a_actor")
  private val sourceB = ActorRef[String]("source_b_actor")
  private val sourceC = ActorRef[String]("source_c_actor")
  private val llm     = ActorRef[String]("llm_actor")

  def receive(msg: String)(using ctx: ActorContext): String =
    ctx.emit(s"Researching '$msg' from 3 sources...")

    val combined = ctx.askAll(
      sourceA -> msg,
      sourceB -> msg,
      sourceC -> msg
    )

    ctx.emit("Summarizing results...")
    llm.ask(s"Summarize: $combined")

// ── 支付 actor: supervision ──

object PaymentActor extends Actor:
  def receive(msg: String)(using ctx: ActorContext): String =
    ctx.withPolicy(maxRetries = 5)

    val orderId = ctx.load("order_id")
    ctx.emit(s"Processing payment for order $orderId...")

    val gateway = ctx.actorRef[String]("payment_gateway")
    val result = gateway.ask(s"charge:$msg:$orderId")

    ctx.save("payment_status", s"completed:$result")
    result

// ── 注册所有 actor ──

object ActorRegistry:
  def registerAll(): Unit =
    ActorSystem.register("llm_actor", LlmActor)
    ActorSystem.register("translator_actor", TranslatorActor)
    ActorSystem.register("chat_actor", ChatActor)
    ActorSystem.register("audit_actor", AuditActor)
    ActorSystem.register("researcher_actor", ResearcherActor)
    ActorSystem.register("payment_actor", PaymentActor)
