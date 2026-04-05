package agent.examples

import agent.{AgentOp, Ctx}
import agent.AgentOp.*

// ══════════════════════════════════════════════════════════════
// 用户接入指南
//
// 你只需要写一个函数: (Ctx, String) => String
// 框架处理所有其他事情: 序列化、恢复、重试、并行、流式输出
// ══════════════════════════════════════════════════════════════

object UserGuide:

  // ─────────────────────────────────────────────
  // Level 1: 最简 agent — 一个函数，一个 effect
  // ─────────────────────────────────────────────

  /** 翻译 agent: 调用 LLM 翻译文本 */
  def translator(ctx: Ctx, msg: String): String =
    ctx.effect(Ask("llm", s"Translate to English: $msg"))

  // ─────────────────────────────────────────────
  // Level 2: 带状态 — load / save
  // ─────────────────────────────────────────────

  /** 待办清单 agent: 持久化任务列表 */
  def todoList(ctx: Ctx, msg: String): String =
    val raw = ctx.effect(LoadState("todos"))
    val todos = if raw.isEmpty then List.empty[String] else raw.split("\n").toList

    msg.split(" ", 2) match
      case Array("add", task) =>
        val updated = todos :+ task
        ctx.effect(SaveState("todos", updated.mkString("\n")))
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
          ctx.effect(SaveState("todos", updated.mkString("\n")))
          s"Done: $removed (remaining: ${updated.size})"

      case _ => s"Unknown command. Use: add <task> | list | done <n>"

  // ─────────────────────────────────────────────
  // Level 3: 流式输出 — emit
  // ─────────────────────────────────────────────

  /** 代码 review agent: 逐步输出分析过程 */
  def codeReviewer(ctx: Ctx, msg: String): String =
    ctx.emit("Analyzing code structure...")
    val structure = ctx.effect(Ask("llm", s"Analyze the structure of:\n$msg"))

    ctx.emit("Checking for bugs...")
    val bugs = ctx.effect(Ask("llm", s"Find bugs in:\n$msg"))

    ctx.emit("Suggesting improvements...")
    val improvements = ctx.effect(Ask("llm", s"Suggest improvements for:\n$msg"))

    ctx.emit("Compiling review report...")
    s"## Structure\n$structure\n\n## Bugs\n$bugs\n\n## Improvements\n$improvements"

  // ─────────────────────────────────────────────
  // Level 4: 并行 — parallel
  // ─────────────────────────────────────────────

  /** 比价 agent: 同时查多个数据源，汇总最优价格 */
  def priceCompare(ctx: Ctx, msg: String): String =
    ctx.emit(s"Querying 3 suppliers for '$msg'...")

    val results = ctx.parallel(
      Ask("supplier_a", s"price:$msg"),
      Ask("supplier_b", s"price:$msg"),
      Ask("supplier_c", s"price:$msg")
    )
    // results 是 JSON 数组: ["price_a", "price_b", "price_c"]

    ctx.emit("Comparing prices...")
    val best = ctx.effect(Ask("llm", s"Which is cheapest? $results"))
    best

  // ─────────────────────────────────────────────
  // Level 5: 容错 — withPolicy
  // ─────────────────────────────────────────────

  /** 支付 agent: 调用第三方支付，自动重试 */
  def payment(ctx: Ctx, msg: String): String =
    ctx.withPolicy(maxRetries = 5)  // 支付接口不稳定，最多重试 5 次

    val orderId = ctx.effect(LoadState("order_id"))
    ctx.emit(s"Processing payment for order $orderId...")

    val result = ctx.effect(Ask("payment_gateway", s"charge:$msg:$orderId"))
    ctx.effect(SaveState("payment_status", s"completed:$result"))
    ctx.effect(Tell("notification", s"Payment completed: $orderId"))
    result

  // ─────────────────────────────────────────────
  // Level 6: 组合 — 一个真实的复杂 agent
  // ─────────────────────────────────────────────

  /** 招聘助手 agent: 多步骤工作流 */
  def recruiter(ctx: Ctx, msg: String): String =
    ctx.withPolicy(maxRetries = 3)

    // 1. 加载候选人信息
    val profile = ctx.effect(LoadState("candidate_profile"))
    ctx.emit(s"Evaluating candidate for: $msg")

    // 2. 并行: 技术评估 + 文化匹配 + 背景调查
    val assessments = ctx.parallel(
      Ask("tech_evaluator", s"Evaluate skills for '$msg': $profile"),
      Ask("culture_fit", s"Check culture fit: $profile"),
      Ask("background_check", s"Verify background: $profile")
    )

    ctx.emit("All assessments complete, generating recommendation...")

    // 3. 汇总生成推荐
    val recommendation = ctx.effect(Ask("llm",
      s"Based on these assessments, should we hire?\n$assessments"))

    // 4. 保存结果 + 通知 HR
    ctx.effect(SaveState("evaluation_result", recommendation))
    ctx.effect(Tell("hr_team", s"Evaluation complete for $msg: $recommendation"))

    recommendation


// ══════════════════════════════════════════════════════════════
// 注册到 Interpreter — 加一行 case 即可
//
//   在 Interpreter.run 的 match 里加:
//
//     case "translator"     => UserGuide.translator(ctx, msg)
//     case "todo"           => UserGuide.todoList(ctx, msg)
//     case "code_reviewer"  => UserGuide.codeReviewer(ctx, msg)
//     case "price_compare"  => UserGuide.priceCompare(ctx, msg)
//     case "payment"        => UserGuide.payment(ctx, msg)
//     case "recruiter"      => UserGuide.recruiter(ctx, msg)
//
// 就这样。不需要改 Rust 代码，不需要改 proto。
// ══════════════════════════════════════════════════════════════
