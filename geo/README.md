# Map boundaries

| File | Source | License |
|---|---|---|
| `us-states.json` | `states-10m.json` from [us-atlas](https://github.com/topojson/us-atlas) 3.0.1 (U.S. Census Bureau cartographic boundaries), state outlines only | ISC, © Michael Bostock |
| `world-countries.json` | `countries-110m.json` from [world-atlas](https://github.com/topojson/world-atlas) 2.0.2 (Natural Earth, public domain), country outlines only | ISC, © Michael Bostock |

Both are TopoJSON with each shape's name in `properties.name`, which is how query results are matched to the map.
