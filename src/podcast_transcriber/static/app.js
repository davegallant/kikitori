document.addEventListener("click", (event) => {
  const timestamp = event.target.closest("[data-seconds]");
  const audio = document.getElementById("episode-audio");
  if (!timestamp || !audio) return;
  audio.currentTime = Number(timestamp.dataset.seconds);
  audio.play();
});

const playerPanel = document.querySelector(".player-panel");
if (playerPanel) {
  const updateStickyTitle = () => {
    const bounds = playerPanel.getBoundingClientRect();
    playerPanel.classList.toggle("is-stuck", bounds.top <= 12 && bounds.bottom > 12);
  };
  window.addEventListener("scroll", updateStickyTitle, { passive: true });
  updateStickyTitle();
}

const statusBox = document.getElementById("job-status");
if (statusBox) {
  const poll = async () => {
    try {
      const response = await fetch(statusBox.dataset.statusUrl);
      if (!response.ok) return;
      const status = await response.json();
      if (status.stage === "complete" || status.stage === "failed") {
        window.location.reload();
        return;
      }
      document.getElementById("stage-label").textContent = status.stage.charAt(0).toUpperCase() + status.stage.slice(1);
    } catch (error) {
      // The next poll will retry a transient local connection failure.
    }
  };
  window.setInterval(poll, 3000);
}
