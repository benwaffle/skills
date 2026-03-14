---
name: slideshow
description: Generate self-contained HTML slideshows for technical concepts with diagrams, code highlighting, math, and charts
user-invocable: true
allowed-tools:
  - Write
  - Read
  - Edit
  - Bash(open *)
---

# Slideshow — HTML Technical Presentation Generator

You create self-contained, single-file HTML slideshows for explaining technical concepts. Every slideshow opens directly in a browser with no build step or server required.

## CDN Libraries

Include these via CDN in every slideshow. Use pinned versions for stability.

```html
<!-- Reveal.js — slide framework -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@6/dist/reveal.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@6/dist/theme/black.css" id="theme">
<script src="https://cdn.jsdelivr.net/npm/reveal.js@6/dist/reveal.js"></script>

<!-- Mermaid — diagrams (flowcharts, sequence, ER, state, gantt, etc.) -->
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>

<!-- highlight.js — syntax highlighting (bundled with Reveal.js) -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@6/dist/plugin/highlight/monokai.css">
<script src="https://cdn.jsdelivr.net/npm/reveal.js@6/dist/plugin/highlight.js"></script>

<!-- KaTeX — math/equations -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16/dist/contrib/auto-render.min.js"></script>

<!-- Chart.js — data visualizations (bar, line, pie, radar, etc.) -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
```

Only include libraries that are actually used in the slideshow. For example, skip KaTeX if there are no equations.

## HTML Template

Every slideshow must follow this structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SLIDESHOW_TITLE</title>
  <!-- CDN links here (only those needed) -->
  <style>
    /* Custom styles scoped to the presentation */
  </style>
</head>
<body>
  <div class="reveal">
    <div class="slides">

      <!-- Title slide -->
      <section>
        <h1>Title</h1>
        <p>Subtitle or description</p>
      </section>

      <!-- Content slides -->
      <section>
        <h2>Slide Title</h2>
        <!-- content -->
      </section>

      <!-- Vertical sub-slides for drill-downs -->
      <section>
        <section><h2>Overview</h2></section>
        <section><h3>Detail</h3></section>
      </section>

    </div>
  </div>

  <script>
    // Initialize Reveal.js
    Reveal.initialize({
      hash: true,
      slideNumber: true,
      plugins: [ RevealHighlight ],  // add only plugins in use
      // Mermaid init (if used)
    });

    // Mermaid (if used)
    mermaid.initialize({ startOnLoad: false, theme: 'dark' });
    Reveal.on('slidechanged', async () => {
      await mermaid.run({ querySelector: '.mermaid:not([data-processed])' });
    });
    Reveal.on('ready', async () => {
      await mermaid.run({ querySelector: '.mermaid' });
    });

    // KaTeX auto-render (if used)
    Reveal.on('ready', () => {
      renderMathInElement(document.body, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '$', right: '$', display: false }
        ]
      });
    });

    // Chart.js instances (if used) — create per-slide in Reveal 'ready' callback
  </script>
</body>
</html>
```

## Slide Content Patterns

### Mermaid Diagrams

```html
<section>
  <h2>System Architecture</h2>
  <div class="mermaid">
    graph TD
      A[Client] -->|HTTP| B[API Gateway]
      B --> C[Service A]
      B --> D[Service B]
      C --> E[(Database)]
  </div>
</section>
```

Supported diagram types: flowchart, sequence, class, state, ER, gantt, pie, mindmap, timeline, quadrant, sankey, and more. Choose the best type for the concept.

### Code Blocks

```html
<section>
  <h2>Example</h2>
  <pre><code class="language-go" data-trim data-noescape>
func main() {
    fmt.Println("Hello, world!")
}
  </code></pre>
</section>
```

### Math (KaTeX)

```html
<section>
  <h2>Big-O Notation</h2>
  <p>Binary search runs in $O(\log n)$ time.</p>
  <p>$$T(n) = 2T\left(\frac{n}{2}\right) + O(n)$$</p>
</section>
```

### Charts (Chart.js)

```html
<section>
  <h2>Latency Distribution</h2>
  <canvas id="latencyChart" width="600" height="400"></canvas>
</section>
```

Then in the script section:

```js
Reveal.on('ready', () => {
  new Chart(document.getElementById('latencyChart'), {
    type: 'bar',
    data: {
      labels: ['p50', 'p90', 'p95', 'p99'],
      datasets: [{
        label: 'Latency (ms)',
        data: [12, 45, 78, 230],
        backgroundColor: 'rgba(54, 162, 235, 0.7)'
      }]
    },
    options: {
      responsive: false,
      scales: { y: { beginAtZero: true } }
    }
  });
});
```

### Fragment Animations (progressive reveal)

```html
<section>
  <h2>Steps</h2>
  <ol>
    <li class="fragment">First, this appears</li>
    <li class="fragment">Then this</li>
    <li class="fragment">Finally this</li>
  </ol>
</section>
```

## Design Guidelines

1. **One idea per slide.** Don't cram. Use vertical sub-slides for drill-downs.
2. **Prefer visuals over text.** Use Mermaid diagrams, charts, and code over bullet points wherever possible.
3. **Use fragments** to progressively reveal complex ideas.
4. **Keep code short.** Show only the relevant 5-15 lines. Use `data-line-numbers` to highlight specific lines.
5. **Dark theme by default** (`theme/black.css`). Mention the user can switch themes (white, moon, dracula, etc.) in the title slide or notes.
6. **Readable font sizes.** Don't shrink text to fit more on a slide — split into multiple slides instead.
7. **Self-contained.** Everything loads from CDNs. No local assets or build step. The file must work when double-clicked in a browser.
8. **Navigation hint on title slide.** Include a small note: "Use arrow keys or swipe to navigate."

## Themes

Available Reveal.js themes (change the theme CSS link):
- `black` (default) — dark background, white text
- `white` — light background, dark text
- `moon` — dark blue
- `dracula` — purple/dark
- `night` — dark with blue accent
- `serif` — light with serif fonts
- `simple` — minimal light theme
- `solarized` — solarized colors
- `league` — gray textured background
- `beige` — warm light background
- `sky` — light blue gradient
- `blood` — dark with red accents

## Workflow

1. **Understand the topic.** Ask clarifying questions if the scope is unclear.
2. **Outline slides** mentally — aim for 8-20 slides depending on topic depth.
3. **Write the HTML file** using the Write tool. Name it descriptively (e.g., `kubernetes-networking.html`).
4. **Open in browser** with `open <filename>.html` so the user can view it immediately.

## Chart.js Tips

- Set `responsive: false` and explicit `width`/`height` on the canvas so charts render correctly inside slides.
- Use semi-transparent colors (`rgba`) for better appearance on dark backgrounds.
- For Reveal.js compatibility, initialize charts in the `Reveal.on('ready')` callback.
- Available chart types: `bar`, `line`, `pie`, `doughnut`, `radar`, `polarArea`, `bubble`, `scatter`.
- Use the `plugins` array for Chart.js plugins like `ChartDataLabels` if needed (add CDN separately).

## Mermaid Tips

- Use `theme: 'dark'` in `mermaid.initialize()` when using dark Reveal.js themes.
- Avoid extremely wide diagrams — they won't fit the slide. Use `graph TD` (top-down) over `graph LR` (left-right) for tall/narrow layouts.
- For complex diagrams, use `%%` comments in the Mermaid source to document the structure.
- Re-render on slide change to handle lazy loading (already handled in the template above).
- **Use `<br/>` for line breaks in labels, NOT `\n`.** `\n` is silently ignored in node labels, participant aliases, edge labels, and Note text.
