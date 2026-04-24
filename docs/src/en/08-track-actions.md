## 8. Track Action

### Track Action (Type 18)
Makes DMX moving heads follow moving objects in real-time during playback.

**How it works:**
1. Create moving objects (props/performers) on the Layout tab
2. Create a Track action on the Actions tab
3. Select target objects and configure assignment
4. During playback, the 40Hz loop computes pan/tilt for each head

**Assignment algorithm:**
- Equal heads and objects: 1:1 mapping
- More heads than objects: Spread evenly across objects
- More objects than heads (cycling mode): Cycle through objects (default 2s per target)
- More objects than heads (fixed mode): Each head locks to one target, extras ignored

**Fields:**
| Field | Description |
|-------|-------------|
| trackObjectIds | Target object IDs (empty = all moving objects, including camera-detected people) |
| trackCycleMs | Cycle time when cycling (default 2000ms) |
| trackOffset | Global [x,y,z] offset in mm |
| trackFixtureIds | Specific fixture IDs (empty = all moving heads) |
| trackFixtureOffsets | Per-fixture [x,y,z] overrides |
| trackAutoSpread | Spread multiple heads across object width |
| trackFixedAssignment | Fixed 1:1 assignment — each head gets one target, extra targets ignored |

---

