// The recreated player (Loop.astro): the playhead runs over the track's duration in real time
// from where the still frame rests, and wraps at the end; stop takes it back to 0:00, previous
// and next to the start. There is
// no audio. Nothing starts by itself with reduced motion, and it rests while off screen or
// while the tab is hidden.
const reduce = matchMedia("(prefers-reduced-motion: reduce)");

const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

for (const player of document.querySelectorAll("[data-player]")) {
  const data = player.dataset;
  const [duration, width] = [data.duration, data.width].map(Number);
  const played = player.querySelector("[data-played]");
  const cursor = player.querySelector("[data-cursor]");
  const time = player.querySelector("[data-time]");
  const toggle = player.querySelector(".player-toggle");
  const [previous, play, next, stop] = ["previous", "play", "next", "stop"].map((act) => player.querySelector(`[data-act="${act}"]`));
  let playing = false; // what the visitor asked for
  let onScreen = false;
  let t = Number(data.start); // seconds into the track
  let last = 0;
  let frame = 0;

  function draw() {
    const x = (t / duration) * width;
    played.setAttribute("width", x.toFixed(2));
    cursor.setAttribute("x", Math.min(width - 2.5, x).toFixed(2));
    time.textContent = clock(t);
  }

  function tick(now) {
    const elapsed = last ? Math.min(now - last, 100) : 0;
    last = now;
    t = (t + elapsed / 1000) % duration;
    draw();
    frame = requestAnimationFrame(tick);
  }

  // Brings the animation in line with what was asked and what can be seen.
  function sync() {
    const run = playing && onScreen && !document.hidden;
    if (run && !frame) {
      last = 0;
      frame = requestAnimationFrame(tick);
    } else if (!run && frame) {
      cancelAnimationFrame(frame);
      frame = 0;
    }
    player.classList.toggle("running", run);
    player.classList.toggle("playing", playing);
    toggle.setAttribute("aria-label", (playing ? data.pauseAnim : data.playAnim) ?? "");
    play.setAttribute("aria-label", (playing ? data.pause : data.play) ?? "");
    draw();
  }

  const flip = () => {
    playing = !playing;
    sync();
  };
  toggle.addEventListener("click", flip);
  play.addEventListener("click", flip);
  // One track, so previous and next both go back to its start, as the app does at the list's ends.
  const restart = () => {
    t = 0;
    sync();
  };
  previous.addEventListener("click", restart);
  next.addEventListener("click", restart);
  stop.addEventListener("click", () => {
    playing = false;
    t = 0;
    sync();
  });

  for (const button of [toggle, previous, play, next, stop]) button.hidden = false;
  playing = !reduce.matches;
  new IntersectionObserver(([entry]) => {
    onScreen = entry.isIntersecting;
    sync();
  }).observe(player);
  document.addEventListener("visibilitychange", sync);
  sync();
}
