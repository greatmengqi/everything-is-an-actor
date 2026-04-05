name := "agent-core"
version := "0.1.0"
scalaVersion := "3.3.3"

// ScalaPB: 从 .proto 生成 Scala case class
Compile / PB.protoSources := Seq(
  (ThisBuild / baseDirectory).value / ".." / "proto"
)
Compile / PB.targets := Seq(
  scalapb.gen(grpc = false) -> (Compile / sourceManaged).value / "scalapb"
)

libraryDependencies ++= Seq(
  "com.thesamet.scalapb" %% "scalapb-runtime" % scalapb.compiler.Version.scalapbVersion % "protobuf",
)

assembly / mainClass := Some("agent.Main")
assembly / assemblyJarName := "agent-core.jar"
assembly / assemblyMergeStrategy := {
  case x if x.endsWith("module-info.class") => MergeStrategy.discard
  case x if x.endsWith(".proto")            => MergeStrategy.first
  case x =>
    val old = (assembly / assemblyMergeStrategy).value
    old(x)
}
