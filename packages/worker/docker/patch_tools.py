"""Apply checked Linux adaptations to the pinned CLI sources before publishing."""

import re
import shutil
import sys
from pathlib import Path


def replace(source: str, old: str, new: str, count: int = 1) -> str:
    if source.count(old) != count:
        raise RuntimeError(f"Pinned source changed: expected {count} occurrences of {old!r}")
    return source.replace(old, new)


fmodel, cue = map(Path, sys.argv[1:])
native = Path(__file__).with_name("NativeTools.cs")
for project in (fmodel / "FModelCLI", cue / "CUE4Parse.CLI"):
    shutil.copyfile(native, project / native.name)

program = fmodel / "FModelCLI/Program.cs"
source = program.read_text(encoding="utf-8-sig")
source = replace(source, "static async Task Main(string[] args)", "static void Main(string[] args)")
source = replace(source, "static void Main(string[] args)\n        {", """static void Main(string[] args)
        {
            if (args.Length == 1 && args[0] == "--check-native-libs")
            {
                NativeTools.SelfTest();
                return;
            }""")
start = source.index("            // Prepare .data directory")
end = source.index("            string gameDir = args[0];", start)
source = source[:start] + "            NativeTools.Initialize();\n" + source[end:]
start = source.index("        private static async Task EnsureDependencies")
source = source[:start] + "    }\n}\n"
program.write_text(source, encoding="utf-8")
project = fmodel / "FModelCLI/FModelCLI.csproj"
source = project.read_text(encoding="utf-8-sig")
source = replace(source, "  <ItemGroup>", "  <ItemGroup>\n    <PackageReference Include=\"SkiaSharp.NativeAssets.Linux.NoDependencies\" Version=\"2.88.9\" />")
project.write_text(source, encoding="utf-8")

program = cue / "CUE4Parse.CLI/Program.cs"
source = program.read_text(encoding="utf-8-sig")
source = replace(source, "private static async Task<int> Main(string[] args)\n    {", """private static async Task<int> Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--check-native-libs")
        {
            NativeTools.SelfTest();
            return 0;
        }""")
source, count = re.subn(
    r"            // Init oodle\n.*?            DetexHelper.Initialize\(detexPath\);",
    "            NativeTools.Initialize();", source, flags=re.DOTALL,
)
if count != 2:
    raise RuntimeError(f"Pinned CUE CLI native initialization changed: {count}")
source = replace(source, "if (!provider.TryLoadPackage(package, out var pkg))", """CUE4Parse.UE4.Assets.IPackage? pkg = null;
            try { pkg = provider.LoadPackage(package); }
            catch (Exception ex) { Console.Error.WriteLine($"LoadPackage failed: {ex}"); }
            if (pkg == null)""")
program.write_text(source, encoding="utf-8")
project = cue / "CUE4Parse.CLI/CUE4Parse.CLI.csproj"
source = project.read_text(encoding="utf-8-sig")
source = replace(source, '        <PackageReference Include="System.CommandLine"',
                 '        <PackageReference Include="SkiaSharp.NativeAssets.Linux.NoDependencies" Version="2.88.9" />\n        <PackageReference Include="System.CommandLine"')
project.write_text(source, encoding="utf-8")
paths = cue / "CUE4Parse-Conversion/ExportSession.cs"
source = paths.read_text(encoding="utf-8-sig")
source = replace(source, "return fullPath.Replace('/', '\\\\');", "return fullPath;")
paths.write_text(source, encoding="utf-8")
