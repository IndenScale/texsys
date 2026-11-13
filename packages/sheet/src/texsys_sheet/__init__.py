"""Engineering sheet composition as a declared build-DAG consumer."""

from texsys.evidence import write_json
from texsys.packages import TexsysPackage
from texsys.targets import Artifact, TargetDefinition, TargetResult

from .compiler import SheetSource, compile_sheet


def sheet_inputs(context):
  # Resolve the same source graph used by the compiler. Missing/malformed input
  # fails before cache restoration, rather than reusing an obsolete sheet.
  source = SheetSource(context)
  source.load()
  return tuple(sorted(source.project_paths))


def build_sheet(context):
  source = SheetSource(context)
  document = source.load()
  svg, report = compile_sheet(source, document)
  output = context.build_path / (context.output or "drawings/sheets/sheet.svg")
  if output.suffix != ".svg":
    raise ValueError("Sheet output must use the .svg extension.")
  evidence = output.with_name(f"{output.stem}-sheet.json")
  output.parent.mkdir(parents=True, exist_ok=True)
  output.write_text(svg, encoding="utf-8")
  write_json(evidence, report)
  return TargetResult(artifacts=(
    Artifact("sheet-svg", output.relative_to(context.root).as_posix()),
    Artifact("sheet-sources", evidence.relative_to(context.root).as_posix(), "diagnostic"),
  ), details={"deliverable": "sheet", "drawing_number": report["metadata"]["drawing_number"],
              "composition": "valid", "engineering_acceptance": "not-evaluated"})


class SheetPackage(TexsysPackage):
  name = "sheet"
  version = "0.1.0"

  def targets(self):
    return (TargetDefinition("sheet:compose", self.name, self.version, build_sheet,
                             input_patterns=sheet_inputs, named_output=True),)


package = SheetPackage()
