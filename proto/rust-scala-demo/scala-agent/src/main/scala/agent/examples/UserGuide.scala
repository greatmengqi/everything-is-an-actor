package agent.examples

import agent.actor.*
import agent.pb.agent.OnExhaust

// ══════════════════════════════════════════════════════════════
// 用户接入指南
//
// 你只需要:
//   1. 继承 Actor，实现 receive
//   2. 用 ActorRef 引用其他 actor
//   3. 调 ActorSystem.register 注册
//
// 框架处理: 序列化、恢复、重试、并行、流式输出
// ══════════════════════════════════════════════════════════════

// ─────────────────────────────────────────────
// Level 1: 最简 actor — 一个 ask 调用
// ─────────────────────────────────────────────

object TranslatorExample extends Actor:
  private val llm = ActorRef[String]("llm_actor")

  def receive(msg: String)(using ctx: ActorContext): String =
    llm.ask(s"Translate to English: $msg")

// ─────────────────────────────────────────────
// Level 2: 带状态 — load / save
// ─────────────────────────────────────────────

object TodoListExample extends Actor:
  def receive(msg: String)(using ctx: ActorContext): String =
    val raw = ctx.load("todos")
    val todos = if raw.isEmpty then List.empty[String] else raw.split("\n").toList

    msg.split(" ", 2) match
      case Array("add", task) =>
        val updated = todos :+ task
        ctx.save("todos", updated.mkString("\n"))
        s"Added: $task (total: ${updated.size})"

      case Array("list") =>
        if todos.isEmpty then "No todos"
        else todos.zipWithIndex.map((t, i) => s"  ${i + 1}. $t").mkString("\n")

      case Array("done", idx) =>
        val i = idx.toInt - 1
        if i < 0 || i >= todos.size then s"Invalid index: $idx"
        else
          val removed = todos(i)
          val updated = todos.patch(i, Nil, 1)
          ctx.save("todos", updated.mkString("\n"))
          s"Done: $removed (remaining: ${updated.size})"

      case _ => "Usage: add <task> | list | done <n>"

// ─────────────────────────────────────────────
// Level 3: 流式输出 — emit
// ─────────────────────────────────────────────

object CodeReviewerExample extends Actor:
  private val llm = ActorRef[String]("llm_actor")

  def receive(msg: String)(using ctx: ActorContext): String =
    ctx.emit("Analyzing code structure...")
    val structure = llm.ask(s"Analyze the structure of:\n$msg")

    ctx.emit("Checking for bugs...")
    val bugs = llm.ask(s"Find bugs in:\n$msg")

    ctx.emit("Suggesting improvements...")
    val improvements = llm.ask(s"Suggest improvements for:\n$msg")

    ctx.emit("Compiling review report...")
    s"## Structure\n$structure\n\n## Bugs\n$bugs\n\n## Improvements\n$improvements"

// ─────────────────────────────────────────────
// Level 4: 并行 — askAll
// ─────────────────────────────────────────────

object PriceCompareExample extends Actor:
  private val supplierA = ActorRef[String]("supplier_a")
  private val supplierB = ActorRef[String]("supplier_b")
  private val supplierC = ActorRef[String]("supplier_c")
  private val llm       = ActorRef[String]("llm_actor")

  def receive(msg: String)(using ctx: ActorContext): String =
    ctx.emit(s"Querying 3 suppliers for '$msg'...")

    val results = ctx.askAll(
      supplierA -> s"price:$msg",
      supplierB -> s"price:$msg",
      supplierC -> s"price:$msg"
    )

    ctx.emit("Comparing prices...")
    llm.ask(s"Which is cheapest? $results")

// ─────────────────────────────────────────────
// Level 5: 容错 — withPolicy
// ─────────────────────────────────────────────

object PaymentExample extends Actor:
  private val gateway = ActorRef[String]("payment_gateway")

  def receive(msg: String)(using ctx: ActorContext): String =
    ctx.withPolicy(maxRetries = 5)

    val orderId = ctx.load("order_id")
    ctx.emit(s"Processing payment for order $orderId...")

    val result = gateway.ask(s"charge:$msg:$orderId")

    ctx.save("payment_status", s"completed:$result")
    val notify = ctx.actorRef[String]("notification")
    notify.tell(s"Payment completed: $orderId")
    result

// ─────────────────────────────────────────────
// Level 6: 组合 — 完整工作流
// ─────────────────────────────────────────────

object RecruiterExample extends Actor:
  private val techEval  = ActorRef[String]("tech_evaluator")
  private val culture   = ActorRef[String]("culture_fit")
  private val bgCheck   = ActorRef[String]("background_check")
  private val llm       = ActorRef[String]("llm_actor")
  private val hr        = ActorRef[String]("hr_team")

  def receive(msg: String)(using ctx: ActorContext): String =
    ctx.withPolicy(maxRetries = 3)

    val profile = ctx.load("candidate_profile")
    ctx.emit(s"Evaluating candidate for: $msg")

    // 并行: 技术 + 文化 + 背景
    val assessments = ctx.askAll(
      techEval -> s"Evaluate skills for '$msg': $profile",
      culture  -> s"Check culture fit: $profile",
      bgCheck  -> s"Verify background: $profile"
    )

    ctx.emit("All assessments complete, generating recommendation...")
    val recommendation = llm.ask(s"Based on these assessments, should we hire?\n$assessments")

    ctx.save("evaluation_result", recommendation)
    hr.tell(s"Evaluation complete for $msg: $recommendation")

    recommendation

// ─────────────────────────────────────────────
// 注册
// ─────────────────────────────────────────────

object ExampleRegistry:
  def registerAll(): Unit =
    ActorSystem.register("translator_ex", TranslatorExample)
    ActorSystem.register("todo_ex", TodoListExample)
    ActorSystem.register("code_reviewer_ex", CodeReviewerExample)
    ActorSystem.register("price_compare_ex", PriceCompareExample)
    ActorSystem.register("payment_ex", PaymentExample)
    ActorSystem.register("recruiter_ex", RecruiterExample)
