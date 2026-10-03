# SeaLouse
This is a Blender plugin to import and export the KMS (in-game model), EVM (cutscene model), and ZMS (cardboard box models) formats in Metal Gear Solid 2: Sons of Liberty. PS2 and Master Collection files can be imported; model exports target the Master Collection, with CMDL supplements generated from the exported models.

## How to install
Click the green "Code" button and "Download ZIP". Then, in Blender, go to Edit > Preferences > Add-ons > Install and select the zip file. It should be immediately added to your File > Import and File > Export menus.

## How to use
There are still several restrictions on how the exporter works, so I'll go over those, and the recommended workarounds, for each file type.

KMS:

1. Each KMS model is split into several meshes, which are split into several vertex groups. Meshes are separated as Blender meshes. Vertex groups are separated as Blender materials, even though it's possible for two vertex groups (even on the same mesh) to have the same textures. 
2. KMS has a limit of two bone weights per vertex, and they must specifically be the bone corresponding to the current mesh and its parent. The Object > SeaLouse > Split by bone option will attempt to automatically split your selected mesh accordingly, which you can then join to the MGS2 model to match transformations.
3. Materials are exported based on primarily their node tree, accessing nodes by name and image metadata to check their texture ID, but if that fails the exporter will fall back on "colorMapFallback", "specularMapFallback", and "environmentMapFallback" custom properties. You can add these to your custom materials, since they likely won't have the same node structure as MGS2 materials. There is also a "flag" custom property. Imported flags are preserved; if missing, flags are derived from the packet's bone weights, texture slots, and mesh culling flags. The environment slot uses reflection mapping; other effect modes require an explicit flag.

CTXR imports match texture names using the model's .tri strcode and each texture strcode. When that pair resolves to multiple textures, the importer asks which **Texture name** to load, with stage names shown as context.

ZMS:

Both PS2 and MC archives are supported. Each member is an independent editable KMS model, named by its archive ID and displayed side by side by default. Editing one member does not change the others, even when they originally shared data in the archive. The display spacing is not exported.

Geometry, UVs, materials and weights use the same editing rules as KMS. Assign bone weights to added vertices.

**Display damage states** shows `cbx.zms` members in columns, with intact, damaged, and broken-debris states in rows. Edit geometry, UVs, weights, and material assignments in Edit Mode; shared parts update in every state that uses them. For object transforms, modifiers, or bone-group changes, disable this option on import and edit the source members directly.

EVM:

1. For EVM models, it's all one mesh, but still split into vertex groups. It's recommended to join your custom model so that it resembles the mesh structure of the model you're editing.
2. EVM has a limit of four bone weights per vertex, but also a limit of 8 distinct bones per vertex group (recall, a vertex group is a material). You can split off a region of geometry that you know uses 8 distinct bones or less, and click the number next to the material on the resulting split mesh to create a single-user copy and ensure it will remain a separate material when re-joining.
3. EVM materials are exactly the same with regards to texture IDs. Imported flags are preserved; if missing, flags are derived from the texture slots and the material's backface culling setting.

Editing and export:

- Assign bone weights to added geometry before exporting.
- Deploy the exported model together with its matching CMDL supplements.

Besides that... I think most of the CMDL code could work for MGS3, but I don't have the main MDL for that game handled at all. Some models seem to have a lower vertex limit in modification than others, be careful. The exporter may alter the normals, even if a model is re-exported with no changes. If something doesn't seem to work, try exporting with no changes and then apply modifications piecemeal until you can identify the issue.

Have fun!
