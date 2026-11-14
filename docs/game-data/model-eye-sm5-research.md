# SM5 eye PS 152039: procedural eye color and HeightLightMap

Scope: SM5 High FGPUSkinPassthrough ordinary base-pass PS 152039, matching VS 59643. ResourceHash A5ED7FACAE28CE578248802080AA16FEF57D3B89. Instruction numbers below refer to the numbered statements in the earlier hsin-denia-eye-sm5-report.md; verify exact instructions in eye-sm5-high/152039.asm.txt. This is a GPU-pixel-shader formula, not final displayed light-transport color. `sat(x)=min(max(x,0),1)`; `lerp(a,b,t)=a+t*(b-a)` without saturating t.

## Resources and source variables

Cooked resource tokens and their validation are in eye-sm5-binding-check.py. t0/s1 = HeightLightMap; t1/s2 = EM; t2/s3 = MainTex; t3 = MaskTex (not read in the color fragment). t0/t1/t2 use sample_b with cb0[149].y bias. The selected SM5 compilation binds HeightLightMap to T_EyesSecondHeightLight07_EG, EM to T_HDMF_EM; actual runtime bindings/sampler/color-space/dynamic overrides are not captured. `u=v3.xy` = UV0; `g=v2.y` = interpolated vertex-color green; `time=cb0[148].z`. MainTex is sampled with the EM.r traversal documented in hsin-denia-eye-sm5-report.md; let M be its RGBA sample at uvMainR. `B=M.rgb*MainTexColorTint.rgb + H`, where H is the EM highlight from that report. In particular B depends on view-dependent ray traversal; it is not simply a UV0 MainTex sample. EM.g at UV0 is `Eg`. HLSL can encounter divisions by zero; none of the additional guards below are asserted.

## Procedural ring, complete instructions 195–228

```
phase = (sin(time * ScaleTransition_Speed * 6.283185) + 1) * .5
shake = sin(time * LightShakeSpeed * 6.283185) * LightShakeScale
origin = (ShapePosition01X, ShapePosition01Y)
       + (LightShakPositionX, LightShakPositionY)*shake
       + (LightPositionX, LightPositionY)
d = u - origin
angle = RotationAnlgle01 * 6.283185
rotated = (dot(d,(cos(angle),-sin(angle))),
           dot(d,(sin(angle), cos(angle))))
shapeScale = (ShapeScaleX,ShapeScaleY)
if UseEyeScaleTransition >= .5:
    shapeScale *= 1 - phase * EyeLight_TransitionScale * .75
shapeScale = (shapeScale + LightNormlScale*(10,20)) * LightDifferentScale
radius = length(rotated * shapeScale)
core = sat((1 - 2*radius) * 100000)
t = sat((1 - radius*1.538462)*1.886792)
triangle = 1 - abs(2*t-1)
lo = EdgeWidth*1.22 - .22
hi = EdgeWidth + .22
z = sat((triangle-lo)/(hi-lo))
edgeMask = z*z*(3-2*z)
inner = B + core                        # scalar broadcast, no saturation
ring = lerp(inner, EdgeColor.rgb, edgeMask)
Cprocedural = lerp(B, ring, UseProLight) # no saturation, Hsin uses 12
```

The decimal constants are the exact disassembly literals (they approximate 1/.65 and 1/.53 but should not be silently replaced in a bit-exact reconstruction). `UseEyeScaleTransition` is the same comparison flag used for height-map scaling; procedural scale uses `1-.75*phase*EyeLight_TransitionScale`, unlike the map scale `1+phase*EyeLight_TransitionScale`. The procedural origin uses ShapePosition01; it does not use SecondLight_Position or the vertex green channel.

## HeightLightMap UV and exact RGB combination, instructions 85–123, 229–236

```
mapScale = 1 + phase*EyeLight_TransitionScale if UseEyeScaleTransition >= .5 else 1
width = (.225*(SecondLight_WidthX,SecondLight_WidthY)*mapScale
         - (LightNormlScale,LightNormlScale))*LightDifferentScale
pos = (SecondLight_PositionX,SecondLight_PositionY)
    + g*(SecondLight_Position_02X,SecondLight_Position_02Y)
    + (LightShakPositionX,LightShakPositionY)*shake
    + (LightPositionX+.05, LightPositionY)
a = (u-pos)/width
c = cos(RotateAngle); s = sin(RotateAngle) # angle directly in radians
uvHL = (c*a.x-s*a.y+.5, s*a.x+c*a.y+.5)
h = sample_b(HeightLightMap, uvHL, bias=cb0[149].y).rgb
C1 = lerp(Cprocedural, HeightLightColor.rgb, h.r)
C2 = lerp(C1, OutLineColor.rgb*OutLineColor.a, h.g)
C3 = lerp(C2, B, LightDisable)
blueTerm = h.b*LightEmissiveInt*(1-LightDisable)
```

No clamp on h, LightDisable, or lerp coefficients is emitted here. The shader applies the RGB branches independently, in this order. Blue is added later to each channel of the separately calculated o0 path; it is not part of C3. The diagnostic PNG has G=0 across the image, but shader code itself does not enforce G=0. `HeightLightColor` and `HeightLight_Color` are distinct parameters.

## Remaining color terms and outputs, instructions 237 onward

```
Csim = C3 * EyeLightSimulation_Color.rgb * EyeLightSimulation_Color.a
isTongKong = 1.0 if Eg <= .99 else 0.0
P = lerp(EyeLightSimulation_YanBai, EyeLightSimulation_TongKong, isTongKong)
distanceFactor = sat(2*length(u-P.xy)/P.z)
power = 0 if distanceFactor <= 0 else exp2(log2(distanceFactor)*P.w)
Csim *= 1-power
C0 = Csim + blueTerm
E0 = M.a * OpacityIntensity * EmissionColor.rgb
E1 = B * isTongKong * (1.2,.550589,.315294)
C0 += lerp(E0,E1,cb4[4].z)
C0 += v5.x * E_Motion_Emissive.rgb
C0 = lerp(C0, SelectionColor.rgb, SelectionColor.a)
L = dot(C3,(.3,.59,.11))
o3.rgb = lerp(C3,(L,L,L),Desaturation)
o0.rgb = max(C0,0)
if cb2[42].w == 0: o0.rgb *= cb0[285].rgb
# optional engine debug substitution if cb0[146].x > 0: asm lines after max
```

M.a is the *same* MainTex sample as M.rgb (EM.r traversal), not the later EM.g-traversal alpha sample. The former remains in r7.w while r7.xyz is overwritten. `cb4[4].z`, `v5.x`, pre-exposure cb0[285], debug and renderer consumption are not determined by material JSON. o0 is finally clamped to nonnegative again by `-min(-r4,0)`; the debug substitution can replace it after pre-exposure. o3 is a separate output based on C3, without the blue term, radial simulation, or the terms controlled by cb4 and v5. Do not equate either output directly with the final lit pixel.

## CB5 evidence and parameter values

The uniform expression set has 94 vector preshaders followed by 165 scalar preshaders, packed four scalars per CB5 vector. The diagnostic eye-cb5-decode.py reads opcode offsets and base64 opcode data, resolves parameter indices, and prints the postfix expressions; eye-cb5-values.py and eye-cb5-values.txt provide child overrides and compiled defaults. Do not interpret a derived CB5 vector as one direct material parameter. Relevant register expressions:

- [41].xyz = E_Motion_Emissive.rgb; [43].xy=.225*SecondLight_WidthXY; [44].xy=LightNormlScale.xx; [46].xy=SecondLight_PositionXY; [47].xy=SecondLight_PositionXY+SecondLight_Position_02XY; [48].xy=LightPositionXY.
- [51].xy=HeightLight_WidthXY; [52].xy=HeightLight_PositionXY; [54].xyz=HeightLight_Color.rgb; [56].xyz=ParallaxOffset.xyz; [57].xyz=-ParallaxOffset.xyz; [60].xy=EyeScale*EyeSize.xy; [62].xyz=MainTexColorTint.rgb.
- [63].xy=(cos(a),-sin(a)), [64].xy=(sin(a),cos(a)), a=RotationAnlgle01*6.283185; [65].xy=(ShapeScaleX,ShapeScaleY); [66].xy=LightNormlScale*(10,20).
- [68].xyz=EdgeColor.rgb; [70].xyz=HeightLightColor.rgb; [73].xyz=OutLineColor.rgb*OutLineColor.a; [74].xyz=EyeLightSimulation_Color.rgb*EyeLightSimulation_Color.a; [77]=EyeLightSimulation_TongKong; [78]=EyeLightSimulation_YanBai; [80].xyz=EmissionColor.rgb; [81].xyz=SelectionColor.rgb.
- [110].z=ScaleTransition_Speed, .w=EyeLight_TransitionScale; [111].x=UseEyeScaleTransition, .z=LightDifferentScale, .w=LightShakeSpeed; [112].x=LightShakeScale, .y=LightShakPositionX; [113].z=LightShakPositionY; [114].y=RotateAngle, .z=LightEmissiveInt, .w=LightDisable.
- [116].y=HeightLightScale, .z=HeightLight_Color.a, .w=ParallaxHeight; [118].y=ShapePosition01X, .z=ShapePosition01Y; [120].z=.75*EyeLight_TransitionScale; [121].y=EdgeWidth+.22, .z=EdgeWidth*1.22-.22, .w=UseProLight; [122].y=OpacityIntensity, .z=SelectionColor.a; [123].z=Desaturation.

For Hsin / Denia Eye1 / Denia Eye2 respectively (child instance overrides, otherwise compiled default; MI_Char_Eye parent separately inspected):

| Parameter | Hsin | Denia Eye1 | Denia Eye2 |
|---|---:|---:|---:|
| ShapePosition01X | 1 | 1.062229 | 1.062229 |
| ShapePosition01Y | .63 | .613 | .613 |
| RotationAnlgle01 | 0 | .495238 | .495238 |
| ShapeScaleX | 6 | 10 | 10 |
| ShapeScaleY | 10 | 14 | 14 |
| EdgeWidth | .6 | .6 | .6 |
| UseProLight | 12 | 1 | 1 |
| SecondLight_WidthX | .644574 | .866215 | .313834 |
| SecondLight_WidthY | .415261 | .664286 | .426910 |
| SecondLight_PositionX | .431905 | .478810 | .459762 |
| SecondLight_PositionY | .382857 | .340952 | .321904 |
| SecondLight_Position_02X | .038095 | -.049999 | -.021428 |
| SecondLight_Position_02Y | -.002857 | 0 | 0 |
| RotateAngle | 0 | .042938 | .042938 |
| LightEmissiveInt | .8 | .2 | .2 |

Shared static values: UseEyeScaleTransition=0, LightDifferentScale=1, LightNormlScale=0, LightPositionXY=(0,0), LightShakPositionXY=(0,0), LightDisable=0, OpacityIntensity=1, Desaturation=0, EdgeColor.rgb=(0,0,0), OutLineColor=(.039546,.023153,.234551,1). HeightLightColor.rgb Hsin=(.029557,.029557,.029557), Denia=(.502685,.512441,.517708). EyeLightSimulation_Color.rgb, EmissionColor.rgb, SelectionColor.a and E_Motion_Emissive.rgb are zero in the inspected child/parent/default data; runtime overrides remain possible. Therefore with these static values Csim, E0, v5 motion and selection terms vanish, but E1 may remain if cb4[4].z is nonzero. The static simplification of the o0 pre-exposure input is `blueTerm + cb4[4].z * B * isTongKong * (1.2,.550589,.315294)`, subject to actual runtime bindings and dynamic state.

Limit: no game capture or runtime buffer contents; conditional material-instance values do not establish which draw/permutation is active. Exact effects of sample filtering, mip bias, texture color views, v5.x, cb4, and exposure are not observed. Texture paths, base H, EM traversal and MaskTex are documented in hsin-denia-eye-sm5-report.md. No project code was changed in this investigation.
