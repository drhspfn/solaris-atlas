# Effect material preview

The WaterMatCap and ParallaxSubUV character effect parent instances use their
explicit `Base` texture as a static preview on the mesh's render UV. The preview
uses the texture RGB as base color and texture alpha multiplied by
`Base_Alpha_Multiply` (default 1) as opacity. This is an approximation, not a
reconstruction of the unlit game shaders. It does not reproduce UV animation,
authored UV transforms, tint, secondary textures, masks, vertex alpha, parallax,
noise, dissolve, matcap or refraction. In particular, the Suisui mountains
material uses vertex alpha; the compact preview currently removes vertex colors.
The original master graphs and the water Base texture were unavailable in the
retained investigation, so visual fidelity and alpha semantics are unverified.
Other effect parents are not inferred from a `Base` parameter.
