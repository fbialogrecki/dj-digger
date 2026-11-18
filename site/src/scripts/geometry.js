// The tonearm's geometry: where it rests and where it sits on the first and last groove.
// Units are the SVG's: the disc is centred on 0,0 with radius 96.
export const PIVOT = [112, -84]; // tonearm bearing, off the disc to the upper right
export const ARM = 132; // bearing to stylus
export const OUTER = 93; // first groove
export const INNER = 38; // last groove, at the label's edge

const reach = Math.hypot(...PIVOT);
const toward = (Math.atan2(-PIVOT[1], -PIVOT[0]) * 180) / Math.PI; // bearing -> spindle
const open = (radius) => (Math.acos((reach ** 2 + ARM ** 2 - radius ** 2) / (2 * reach * ARM)) * 180) / Math.PI;

// The arm's angle with the stylus on a groove of that radius, and parked beside the disc.
export const armAt = (radius) => toward - open(radius);
export const ARM_START = armAt(OUTER);
export const ARM_END = armAt(INNER);
export const ARM_REST = armAt(104);
