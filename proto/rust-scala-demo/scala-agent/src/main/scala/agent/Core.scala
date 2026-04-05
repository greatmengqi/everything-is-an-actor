package agent

import agent.pb.agent.*
import java.io.{DataInputStream, DataOutputStream}

// ══════════════════════════════════════════════════════════════
// 1. Replay Context
// ══════════════════════════════════════════════════════════════

sealed trait AgentOp
object AgentOp:
  case class Ask(target: String, payload: String)       extends AgentOp
  case class Tell(target: String, payload: String)       extends AgentOp
  case class LoadState(key: String)                      extends AgentOp
  case class SaveState(key: String, value: String)       extends AgentOp
  case class Parallel(ops: List[AgentOp])                extends AgentOp  // 并行 effect

class Suspend(
  val op: AgentOp,
  val nextStep: Int,
  val results: Map[Int, String],
  val emissions: List[String],
  val policy: Option[SupervisionPolicy]
) extends Exception(null, null, true, false)

class Ctx private[agent] (resumeAt: Int, saved: Map[Int, String]):
  private var step = 0
  private var _policy: Option[SupervisionPolicy] = None
  private var _emissions = List.empty[String]

  /** 声明 supervision 策略 — 跟着 ContState 序列化 */
  def withPolicy(maxRetries: Int, onExhaust: OnExhaust = OnExhaust.STOP): Unit =
    _policy = Some(SupervisionPolicy(maxRetries, onExhaust = onExhaust))

  /** 流式输出 — 非暂停点，replay 时跳过 */
  def emit(item: String): Unit =
    if step >= resumeAt then _emissions = _emissions :+ item

  /** 单个 effect — 暂停点 */
  def effect(op: AgentOp): String =
    val myStep = step
    step += 1
    if myStep < resumeAt then saved(myStep)
    else throw Suspend(op, myStep + 1, saved, _emissions, _policy)

  /** 并行 effect — 暂停点，返回 JSON 数组字符串 */
  def parallel(ops: AgentOp*): String =
    effect(AgentOp.Parallel(ops.toList))

  private[agent] def policy: Option[SupervisionPolicy] = _policy
  private[agent] def emissions: List[String] = _emissions

// ══════════════════════════════════════════════════════════════
// 2. Agent 定义
// ══════════════════════════════════════════════════════════════

object Agents:
  import AgentOp.*

  def assistant(ctx: Ctx, msg: String): String =
    val history = ctx.effect(LoadState("chat_history"))
    val prompt  = if history.isEmpty then s"User: $msg"
                  else s"$history\nUser: $msg"
    ctx.emit(s"Thinking about: $msg")
    val response = ctx.effect(Ask("llm", prompt))
    ctx.emit("Got response, saving...")
    ctx.effect(SaveState("chat_history", s"$prompt\nAssistant: $response"))
    ctx.effect(Tell("audit", s"processed:$msg"))
    response

  def counter(ctx: Ctx, msg: String): String =
    val raw   = ctx.effect(LoadState("count"))
    val count = if raw.isEmpty then 0 else raw.toInt
    val next  = msg match
                  case "inc" => count + 1
                  case "dec" => count - 1
                  case _     => count
    ctx.effect(SaveState("count", next.toString))
    s"count=$next"

  /** Supervision demo: 调用不稳定服务，声明重试策略 */
  def flaky(ctx: Ctx, msg: String): String =
    ctx.withPolicy(maxRetries = 3)
    val result = ctx.effect(Ask("unreliable_service", msg))
    s"flaky got: $result"

  /** Parallel + Streaming demo: 并行查询 3 个源，汇总 */
  def researcher(ctx: Ctx, msg: String): String =
    ctx.emit(s"Researching '$msg' from 3 sources...")
    val combined = ctx.parallel(
      Ask("source_a", msg),
      Ask("source_b", msg),
      Ask("source_c", msg)
    )
    ctx.emit("All sources responded, summarizing...")
    val summary = ctx.effect(Ask("llm", s"Summarize these results: $combined"))
    summary

// ══════════════════════════════════════════════════════════════
// 3. Interpreter
// ══════════════════════════════════════════════════════════════

object Interpreter:

  def start(req: ReceiveRequest): Response =
    run(req.agentType, req.msg, 0, Map.empty, None)

  def resume(req: ResumeRequest): Response =
    val cont = req.getCont
    val updated = cont.results + ((cont.resumeAt - 1) -> req.response)
    run(cont.agentType, cont.msg, cont.resumeAt, updated, cont.policy)

  private def run(agentType: String, msg: String, resumeAt: Int,
                  results: Map[Int, String],
                  policyOverride: Option[SupervisionPolicy]): Response =
    val ctx = Ctx(resumeAt, results)
    try
      val result = agentType match
        case "assistant"  => Agents.assistant(ctx, msg)
        case "counter"    => Agents.counter(ctx, msg)
        case "flaky"      => Agents.flaky(ctx, msg)
        case "researcher" => Agents.researcher(ctx, msg)
        case other        => s"error:unknown_type:$other"
      Response(Response.Response.Done(DoneResponse(result, ctx.emissions)))
    catch
      case e: Suspend =>
        // 合并 policy: agent 声明的优先，否则用 ContState 携带的
        val policy = e.policy.orElse(policyOverride)
        val cont = ContState(agentType, msg, e.nextStep, e.results, policy)
        val effResp = e.op match
          case AgentOp.Parallel(ops) =>
            EffectResponse(
              kind = EffectResponse.Kind.Parallel(ParallelOps(ops.map(toProtoOp))),
              cont = Some(cont),
              emissions = e.emissions
            )
          case _ =>
            EffectResponse(
              kind = EffectResponse.Kind.Single(toProtoOp(e.op)),
              cont = Some(cont),
              emissions = e.emissions
            )
        Response(Response.Response.Effect(effResp))

  private type PBAgentOp = agent.pb.agent.AgentOp
  private val  PBAgentOp = agent.pb.agent.AgentOp

  private def toProtoOp(op: AgentOp): PBAgentOp =
    import AgentOp.*
    op match
      case Ask(t, p)      => PBAgentOp(PBAgentOp.Op.Ask(AskOp(t, p)))
      case Tell(t, p)     => PBAgentOp(PBAgentOp.Op.Tell(TellOp(t, p)))
      case LoadState(k)   => PBAgentOp(PBAgentOp.Op.LoadState(LoadStateOp(k)))
      case SaveState(k,v) => PBAgentOp(PBAgentOp.Op.SaveState(SaveStateOp(k, v)))
      case Parallel(_)    => throw IllegalStateException("Parallel handled at EffectResponse level")

// ══════════════════════════════════════════════════════════════
// 4. 传输层 — length-prefixed protobuf over stdin/stdout
// ══════════════════════════════════════════════════════════════

object Main:
  def main(args: Array[String]): Unit =
    val in  = new DataInputStream(System.in)
    val out = new DataOutputStream(System.out)

    writeFrame(out, Response(Response.Response.Ready(ReadyResponse("0.2.0"))))

    var running = true
    while running do
      try
        val reqBytes = readFrame(in)
        val request  = Request.parseFrom(reqBytes)
        val response = request.request match
          case Request.Request.Receive(req) => Interpreter.start(req)
          case Request.Request.Resume(req)  => Interpreter.resume(req)
          case Request.Request.Empty        => Response(Response.Response.Done(DoneResponse("error:empty_request")))
        writeFrame(out, response)
      catch
        case _: java.io.EOFException => running = false

  private def readFrame(in: DataInputStream): Array[Byte] =
    val len = in.readInt()
    val buf = new Array[Byte](len)
    in.readFully(buf)
    buf

  private def writeFrame(out: DataOutputStream, msg: Response): Unit =
    val bytes = msg.toByteArray
    out.writeInt(bytes.length)
    out.write(bytes)
    out.flush()
