/* Kitty's cat. One drawing, every pose: moods are CSS on these same shapes, so she can't drift off-model.

   <kitty-cat mood="idle"></kitty-cat>                     decorative, hidden from screen readers
   <kitty-cat mood="reviewing" label="Kitty checking"></kitty-cat>   announced with that label
   element.play("celebrating", 2600)                       a moment, then back to the resting mood

   A mood only ever comes from what the page already knows. This file draws, and it talks to nobody. */
(function () {
  const MOODS = ["idle", "greeting", "curious", "thinking", "guiding", "reviewing", "filing", "waiting", "warning", "paid", "reconciled", "celebrating", "sleeping"];

  const SVG = `<svg class="kc" viewBox="0 0 240 240" focusable="false" aria-hidden="true">
  <ellipse class="kc-floor" cx="112" cy="226" rx="74" ry="7"/>
  <g class="kc-jump">
    <g class="kc-tail">
      <path class="kc-tail-fur" d="M152 208C190 216 214 186 200 150"/>
      <path class="kc-tail-tip" d="M204 170C205 162 203.500 156 200 150"/>
    </g>
    <g class="kc-torso">
      <path class="f-fur" d="M60 214C50 176 70 128 112 128C154 128 174 176 164 214C162 226 62 226 60 214Z"/>
      <path class="s-stripe" d="M63 178C70 176 76 178 80 182M60 196C67 194 73 196 77 200"/>
      <path class="s-stripe" d="M161 178C154 176 148 178 144 182M164 196C157 194 151 196 147 200"/>
      <path class="f-cream" d="M88 222C80 182 92 150 112 150C132 150 144 182 136 222Z"/>
      <ellipse class="f-fur-deep" cx="70" cy="218" rx="24" ry="10"/>
      <ellipse class="f-fur-deep" cx="154" cy="218" rx="24" ry="10"/>
    </g>
    <path class="kc-tail-wrap" d="M166 216C150 236 92 236 66 222"/>
    <g class="kc-paws">
      <rect class="f-fur" x="87" y="180" width="22" height="42" rx="11"/>
      <rect class="f-fur" x="115" y="180" width="22" height="42" rx="11"/>
      <path class="s-toe" d="M94 217v4M102 217v4M122 217v4M130 217v4"/>
    </g>
    <g class="kc-collar">
      <path class="s-collar" d="M78 139C94 152 130 152 146 139"/>
      <g class="kc-tag">
        <circle class="f-coin" cx="112" cy="160" r="10.500"/>
        <path class="s-rupee" d="M107 155h10M107 158.500h10M109 155c5.500 0 5.500 7 0 7h-2l7 6"/>
      </g>
    </g>
    <g class="kc-head">
      <g class="kc-ear kc-ear-l">
        <path class="f-fur" d="M54 76L46 18Q46 8 55 13L100 42Z"/>
        <path class="f-ear" d="M62 62L57 28Q57 23 62 26L88 44Z"/>
      </g>
      <g class="kc-ear kc-ear-r">
        <path class="f-fur" d="M170 76L175.500 45L165 39L177.500 31L178 18Q178 8 169 13L124 42Z"/>
        <path class="f-ear" d="M162 62L167 28Q167 23 162 26L136 44Z"/>
      </g>
      <path class="f-fur" d="M48 92C48 54 76 36 112 36C148 36 176 54 176 92C176 124 146 140 112 140C78 140 48 124 48 92Z"/>
      <path class="s-stripe wide" d="M112 40V56M96 42L99 54M128 42L125 54"/>
      <path class="s-stripe" d="M51 92L65 95M54 108L67 106M173 92L159 95M170 108L157 106"/>
      <path class="f-cream" d="M112 96C128 96 142 104 142 117C142 131 128 138 112 138C96 138 82 131 82 117C82 104 96 96 112 96Z"/>
      <g class="kc-eyes">
        <g class="kc-eye kc-eye-l">
          <ellipse class="f-eyewhite" cx="84" cy="88" rx="16" ry="18"/>
          <ellipse class="f-iris" cx="84" cy="88" rx="12.500" ry="15"/>
          <g class="kc-gaze"><ellipse class="f-pupil" cx="84" cy="89" rx="7.500" ry="11"/><circle class="f-glint" cx="88.500" cy="82" r="3.600"/></g>
          <path class="f-fur kc-lid" d="M66 88C66 66 102 66 102 88V66H66Z"/>
        </g>
        <g class="kc-eye kc-eye-r">
          <ellipse class="f-eyewhite" cx="140" cy="88" rx="16" ry="18"/>
          <ellipse class="f-iris" cx="140" cy="88" rx="12.500" ry="15"/>
          <g class="kc-gaze"><ellipse class="f-pupil" cx="140" cy="89" rx="7.500" ry="11"/><circle class="f-glint" cx="144.500" cy="82" r="3.600"/></g>
          <path class="f-fur kc-lid" d="M122 88C122 66 158 66 158 88V66H122Z"/>
        </g>
      </g>
      <path class="s-face e-happy" d="M70 92Q84 74 98 92M126 92Q140 74 154 92"/>
      <path class="s-face e-shut" d="M70 86Q84 98 98 86M126 86Q140 98 154 86"/>
      <path class="s-face kc-brow b-worry" d="M68 64L92 58M156 64L132 58"/>
      <path class="s-face kc-brow b-up" d="M70 62Q84 52 96 60"/>
      <path class="f-nose" d="M105 105Q112 102 119 105Q118 112 112 114Q106 112 105 105Z"/>
      <path class="s-face m-smile" d="M112 114V118M112 118Q106 127 97 121M112 118Q118 127 127 121"/>
      <g class="m-open"><path class="f-mouth" d="M96 119Q112 125 128 119Q126 137 112 137Q98 137 96 119Z"/><path class="f-tongue" d="M104 130Q112 124 120 130Q118 137 112 137Q106 137 104 130Z"/></g>
      <ellipse class="f-mouth m-o" cx="112" cy="123" rx="5" ry="6"/>
      <path class="s-face m-worry" d="M99 124Q105 119 112 123Q119 127 125 122"/>
      <path class="s-face m-rest" d="M112 114V117M105 120Q112 124 119 120"/>
      <path class="s-whisker" d="M72 116L42 110M72 124L44 128M152 116L182 110M152 124L180 128"/>
    </g>
    <g class="kc-prop kc-receipt"><g transform="translate(20 22)">
      <path class="f-paper" d="M152 106l6 5 6-5 6 5 6-5 6 5 6-5v54h-36z"/>
      <path class="s-print" d="M159 124h22M159 133h22M159 142h13"/>
    </g><path class="s-arm" d="M146 186L182 176"/></g>
    <g class="kc-prop kc-stamped"><g transform="translate(22 18)">
      <path class="f-paper" d="M150 104l6.500 5 6.500-5 6.500 5 6.500-5 6.500 5 6.500-5v60h-39z"/>
      <circle class="f-good" cx="169.500" cy="134" r="13"/>
      <path class="s-check" d="M163 134l5 5 9-10"/>
    </g><path class="s-arm" d="M146 188L184 178"/></g>
    <g class="kc-prop kc-coin">
      <path class="s-arm" d="M150 176L190 146"/>
      <circle class="f-coin" cx="202" cy="118" r="25"/>
      <circle class="s-coin-rim" cx="202" cy="118" r="19"/>
      <path class="s-rupee big" d="M194 109h16M194 115h16M197.500 109c9 0 9 12 0 12h-3.500l11 10"/>
    </g>
    <g class="kc-arm kc-arm-r"><rect class="f-fur" x="148" y="108" width="24" height="56" rx="12"/><rect class="f-cream" x="151" y="110" width="18" height="18" rx="9"/></g>
    <g class="kc-arm kc-arm-l"><rect class="f-fur" x="52" y="108" width="24" height="56" rx="12"/><rect class="f-cream" x="55" y="110" width="18" height="18" rx="9"/></g>
    <g class="kc-prop kc-chin"><path class="s-arm" d="M154 190L134 152"/><circle class="f-cream" cx="133" cy="148" r="9"/></g>
    <g class="kc-prop kc-lens">
      <path class="s-handle" d="M150 176L172 202"/>
      <circle class="f-glass" cx="134" cy="160" r="23"/>
      <circle class="s-lens" cx="134" cy="160" r="23"/>
      <path class="s-glint" d="M121 154Q124 145 133 143"/>
      <rect class="f-fur" x="160" y="186" width="26" height="20" rx="10" transform="rotate(44 173 196)"/>
    </g>
  </g>
  <g class="kc-marks">
    <g class="kc-mark kc-dots"><circle cx="186" cy="56" r="5"/><circle cx="204" cy="42" r="6.500"/><circle cx="224" cy="26" r="8"/></g>
    <g class="kc-mark kc-z"><path d="M180 50h14l-14 16h14"/><path d="M204 22h19l-19 22h19"/></g>
    <g class="kc-mark kc-q"><circle cx="208" cy="42" r="20"/><path d="M201 36c0-10 15-10 15-1 0 6-7 6-7 12M209 53v.500"/></g>
    <g class="kc-mark kc-bang"><circle cx="208" cy="42" r="20"/><path d="M208 30v15M208 54v.500"/></g>
    <g class="kc-mark kc-clock"><circle cx="208" cy="42" r="20"/><path d="M208 31v11l8 5"/></g>
    <g class="kc-mark kc-glow"><path d="M210 40l3 9 9 3-9 3-3 9-3-9-9-3 9-3zM40 52l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/></g>
    <g class="kc-mark kc-flowers"><path d="M26 92a8 5 30 1 0 14 8 8 5 30 1 0-14-8z"/><path d="M200 18a8 5-30 1 0 14-8 8 5-30 1 0-14 8z"/><path d="M16 40a7 4 60 1 0 7 12 7 4 60 1 0-7-12z"/><path d="M222 86a7 4-60 1 0-7 12 7 4-60 1 0 7-12z"/></g>
  </g>
</svg>`;

  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  class KittyCat extends HTMLElement {
    static get observedAttributes() { return ["label"]; }
    connectedCallback() {
      if (!this.firstElementChild) this.innerHTML = SVG;
      this.syncLabel();
    }
    attributeChangedCallback() { this.syncLabel(); }
    syncLabel() {
      const label = this.getAttribute("label");
      if (label) { this.setAttribute("role", "img"); this.setAttribute("aria-label", label); this.removeAttribute("aria-hidden"); }
      else { this.removeAttribute("role"); this.removeAttribute("aria-label"); this.setAttribute("aria-hidden", "true"); }
    }
    /* The resting mood. A moment in progress finishes first, then she settles on this. */
    rest(mood) {
      if (!MOODS.includes(mood)) return;
      this._rest = mood;
      if (!this._moment) this.setAttribute("mood", mood);
    }
    play(mood, ms = 2400) {
      if (!MOODS.includes(mood)) return;
      this._rest = this._rest || this.getAttribute("mood") || "idle";
      clearTimeout(this._moment);
      this.setAttribute("mood", mood);
      this._moment = setTimeout(() => { this._moment = null; this.setAttribute("mood", this._rest); }, calm() ? Math.min(ms, 1500) : ms);
    }
  }
  if (!customElements.get("kitty-cat")) customElements.define("kitty-cat", KittyCat);

  /* Marigold petals for the one moment that earns them: a trip that's actually square. */
  function petals() {
    if (calm() || document.querySelector(".kc-petals")) return;
    const box = document.createElement("div");
    box.className = "kc-petals";
    box.setAttribute("aria-hidden", "true");
    const tones = ["#F5A81C", "#E97F22", "#FFC94D", "#C2185B"];
    for (let i = 0; i < 34; i++) {
      const p = document.createElement("i");
      p.style.cssText = `left:${Math.random() * 100}%;background:${tones[i % tones.length]};animation-delay:${Math.random() * 900}ms;animation-duration:${2200 + Math.random() * 1500}ms;--drift:${Math.round(Math.random() * 160 - 80)}px;--spin:${Math.round(Math.random() * 720 - 360)}deg`;
      box.append(p);
    }
    document.body.append(box);
    setTimeout(() => box.remove(), 5000);
  }

  document.addEventListener("visibilitychange", () => document.documentElement.classList.toggle("kc-paused", document.hidden));
  window.Kitty = Object.assign(window.Kitty || {}, { MOODS, petals, calm });
})();
