using System;
using System.IO;
using System.Linq;
using System.Text;
using CUE4Parse.Compression;
using CUE4Parse_Conversion.Textures.BC;
using SkiaSharp;

// Both CLIs use their own bundled directory. No runtime downloads or shared /tmp libraries.
internal static class NativeTools
{
    private static string Require(string name)
    {
        var path = Path.Combine(AppContext.BaseDirectory, ".data", name);
        if (!File.Exists(path)) throw new FileNotFoundException("Bundled native dependency missing", path);
        return path;
    }

    public static void Initialize()
    {
        OodleHelper.Initialize(Require(OodleHelper.OodleFileName));
        ZlibHelper.Initialize(Require(ZlibHelper.DllName));
        DetexHelper.Initialize(Require(DetexHelper.DLL_NAME));
        if (OodleHelper.Instance == null || ZlibHelper.Instance == null)
            throw new InvalidOperationException("Bundled decompressors did not initialize");
    }

    public static void SelfTest()
    {
        Initialize();
        var expected = Encoding.UTF8.GetBytes(string.Concat(Enumerable.Repeat("solaris-native-probe\n", 128)));
        var compressed = File.ReadAllBytes(Require("oodle-probe.bin"));
        var decoded = new byte[expected.Length];
        OodleHelper.Decompress(compressed, 0, compressed.Length, decoded, 0, decoded.Length);
        if (!decoded.SequenceEqual(expected)) throw new InvalidDataException("Oodle probe mismatch");

        compressed = Convert.FromHexString("789ccb4b2cc92c4b5528c9cfcf29e60200244604e3");
        decoded = new byte[13];
        ZlibHelper.Decompress(compressed, 0, compressed.Length, decoded, 0, decoded.Length);
        if (Encoding.UTF8.GetString(decoded) != "native tools\n")
            throw new InvalidDataException("Zlib probe mismatch");

        decoded = DetexHelper.DecodeDetexLinear(new byte[] { 255, 255, 0, 0, 0, 0, 0, 0 },
            4, 4, false, DetexTextureFormat.DETEX_TEXTURE_FORMAT_BC1A, DetexPixelFormat.DETEX_PIXEL_FORMAT_RGBA8);
        var expectedPixel = new byte[] { 248, 252, 248, 255 };
        if (decoded.Length != 64 || decoded.Where((value, index) => value != expectedPixel[index % 4]).Any())
            throw new InvalidDataException($"Detex BC1 probe mismatch: {Convert.ToHexString(decoded)}");

        using var bitmap = new SKBitmap(1, 1);
        bitmap.Erase(SKColors.White);
        using var image = SKImage.FromBitmap(bitmap);
        using var png = image.Encode(SKEncodedImageFormat.Png, 100);
        if (png == null || png.Size == 0) throw new InvalidDataException("Skia PNG probe failed");
        Console.WriteLine("Native tools OK: Oodle roundtrip, Zlib, Detex BC1, Skia PNG");
    }
}
