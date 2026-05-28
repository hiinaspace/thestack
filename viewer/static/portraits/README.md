# Character portraits

The theater viewer (`/theater`) shows a character portrait for the speaking
player in the bottom narration bar. Until art exists, it draws a colored
placeholder (the persona's initial). Drop a PNG here to replace it — no code
change needed.

## Drop-in convention

- File name: `viewer/static/portraits/<persona>.png` (lowercase persona name).
  Current personas: `aria.png`, `bryn.png`, `mira.png`, `noct.png`.
- The portrait box is a tall rectangle (~150×172 on screen, ratio ≈ 0.87).
  Recommended source size **~640×800**, same framing for all four so faces line
  up. Transparent **or** solid dark background both work (the box sits on a dark
  panel and lights up / lifts when the character is the active speaker).
- A static, neutral "default" expression is all that's needed for now. (Later
  we can add an LLM tool that sets an emotion and swaps between sprite variants
  like `aria-smug.png` / `aria-angry.png`; keep the base file as the default.)

## Generation prompts

Shared style (prepend to each): visual-novel / anime character art in the
clean, bold-lined style of *Phoenix Wright*, *Yu-Gi-Oh!*, and *Pokémon* trainer
portraits. Half-body bust, three-quarter "facing the viewer" angle, head and
shoulders prominent, confident readable silhouette, flat cel shading with crisp
ink outlines, saturated palette, soft rim light. Single character, centered,
plain or transparent background, no text, no card frames, no logos. Neutral
default expression with a hint of the character's attitude. Consistent framing
and scale across all four.

**aria — mono-red aggro, short-tempered tournament grinder**
> A young woman radiating impatient energy, mid-retort. Spiky, choppy
> red-and-orange hair with stray embers, sharp amber eyes, a cocky half-smirk.
> Worn tournament-grinder streetwear — open jacket/hoodie over a band tee, a
> lanyard, fingerless gloves — fingers flicking a single card. Hot reds and
> blacks; faint heat-shimmer / sparks around her. Fast, leaning-forward posture.

**bryn — mono-green ramp, patient naturalist**
> A calm, grounded woman who narrates her own creatures like a nature
> documentary. Long deep-green and bark-brown hair with a leaf or two woven in,
> serene half-lidded eyes, gentle knowing smile. Earthy druidic-but-casual
> clothing — layered linens, a vine-wrapped pauldron, moss-green tones. Soft
> dappled forest light; a tiny sprout or curling vine near her shoulder.
> Unhurried, settled posture.

**mira — mono-white disciplined commander**
> A composed tactician who treats combat like a formation drill. Neat
> shoulder-length pale-blonde hair, level steady gaze, faint confident set to
> the jaw. White-silver-and-gold attire — a clean officer's coat or light
> ceremonial armor with crisp edges and a subtle winged/heraldic motif.
> Cool whites and golds, soft halo-like backlight. Upright, exact, commanding
> posture, perhaps one hand raised as if signaling a line to hold.

**noct — mono-black attrition, sardonic accountant of death**
> A cool, forensic figure with the bedside manner of a coroner and the patience
> of a debt collector. Sleek dark hair, pale skin, half-lidded sardonic eyes, a
> thin knowing smirk. Dark formal attire — a high-collared black coat, gloves,
> a faint ledger/quill or raven motif. Deep blacks and bruised purples, low key
> lighting, a single cold highlight. Still, unbothered, faintly menacing
> posture — calm certainty rather than aggression.
