using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;

// Input must be an already-extracted directory containing a uasset/uexp pair.
// Never alter the installed client, and never silently replace an existing export.
if (args.Length != 3)
{
    Console.Error.WriteLine("Usage: ShaderMaterialProbe <input-directory> <asset-filename> <output-json>");
    return 2;
}
var input = Path.GetFullPath(args[0]);
var filename = args[1];
var output = Path.GetFullPath(args[2]);
if (!Directory.Exists(input) || Path.GetFileName(filename) != filename ||
    !filename.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || File.Exists(output))
{
    Console.Error.WriteLine("Invalid input directory, asset filename, or existing output.");
    return 2;
}
var provider = new DefaultFileProvider(input, SearchOption.AllDirectories,
    new VersionContainer(EGame.GAME_WutheringWaves)) { ReadShaderMaps = true };
provider.Initialize();
provider.PostMount();
var matches = provider.Files.Keys.Where(path =>
    Path.GetFileName(path).Equals(filename, StringComparison.OrdinalIgnoreCase)).ToArray();
if (matches.Length != 1 || !provider.TryLoadPackage(matches[0], out var package))
{
    Console.Error.WriteLine($"Expected one readable package; found {matches.Length}.");
    return 1;
}
var json = JsonConvert.SerializeObject(package.GetExports().ToArray(), Formatting.Indented);
using (var stream = new FileStream(output, FileMode.CreateNew, FileAccess.Write))
using (var writer = new StreamWriter(stream))
    writer.Write(json);
Console.WriteLine($"Exported shader-map metadata for {filename}");
return 0;
