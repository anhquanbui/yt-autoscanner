// static/js/video_analytics_charts.js

window.addEventListener("DOMContentLoaded", function () {
  const d = window.videoAnalyticsData;
  if (!d) return;

  // Force numeric labels for safety
  const labels = (d.labels || []).map((x) => Number(x));
  const views = d.views || [];
  const likes = d.likes || [];
  const comments = d.comments || [];
  const highlightHours = d.highlightHours || [1, 3, 6, 12, 24];
  const viralStatusLabel = d.viralStatusLabel || "";
  const behaviorLabel = d.behaviorLabel || "";

  if (!labels.length) return;

  // ---------- Shared helpers ----------

  function getClosestIndex(arr, target) {
    let bestIdx = null;
    let bestDiff = Infinity;
    arr.forEach((val, idx) => {
      const diff = Math.abs(val - target);
      if (diff < bestDiff) {
        bestDiff = diff;
        bestIdx = idx;
      }
    });
    return bestIdx;
  }

  function getHighlightIndexSet(labels, targets) {
    const s = new Set();
    targets.forEach((h) => {
      const idx = getClosestIndex(labels, h);
      if (idx !== null && idx !== undefined) s.add(idx);
    });
    return s;
  }

  // ---------- Chart: line + value labels at checkpoints ----------

  function createLineChart(config) {
    const {
      canvasId,
      seriesLabel,
      values,
      baseColor,
      fillColor,
      checkpointsContainerId,
      checkpointsLabel,
      labels,
      highlightIndexSet,
    } = config;

    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    // Plugin: draw values above highlighted points
    const highlightLabelsPlugin = {
      id: "highlightLabels_" + canvasId,
      afterDatasetsDraw(chart) {
        const meta = chart.getDatasetMeta(0);
        if (!meta || !meta.data) return;

        const points = meta.data;
        const chartCtx = chart.ctx;

        chartCtx.save();
        chartCtx.fillStyle = "#e5e7eb";
        chartCtx.font =
          "600 11px system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
        chartCtx.textAlign = "center";
        chartCtx.textBaseline = "bottom";

        points.forEach((point, index) => {
          if (!highlightIndexSet.has(index)) return;
          const raw = values[index];
          if (raw == null) return;

          const pos = point.tooltipPosition();
          const x = pos.x;
          const y = pos.y;
          const text = Number(raw).toLocaleString();
          const padding = 6;

          chartCtx.fillText(text, x, y - padding);
        });

        chartCtx.restore();
      },
    };

    new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: seriesLabel,
            data: values,
            borderColor: baseColor,
            backgroundColor: fillColor,
            borderWidth: 2,
            tension: 0.25,
            pointRadius: (ctx) =>
              highlightIndexSet.has(ctx.dataIndex) ? 4 : 2,
            pointHoverRadius: (ctx) =>
              highlightIndexSet.has(ctx.dataIndex) ? 6 : 3,
            pointBackgroundColor: baseColor,
            pointBorderColor: "#020617",
            pointBorderWidth: (ctx) =>
              highlightIndexSet.has(ctx.dataIndex) ? 1.5 : 0,
            pointHitRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
          padding: { top: 22 },
        },
        scales: {
          x: {
            title: {
              display: true,
              text: "Hours since publish",
              color: "#cbd5f5",
            },
            ticks: { color: "#9ca3af" },
            grid: { color: "rgba(55, 65, 81, 0.6)" },
          },
          y: {
            title: {
              display: true,
              text: "Count",
              color: "#cbd5f5",
            },
            ticks: {
              color: "#9ca3af",
              callback: (v) => v.toLocaleString(),
            },
            grid: { color: "rgba(55, 65, 81, 0.6)" },
          },
        },
        plugins: {
          legend: {
            labels: { color: "#e5e7eb" },
          },
          tooltip: {
            callbacks: {
              title: (items) =>
                items.length ? `${items[0].label}h since publish` : "",
              label: (ctx) =>
                `${seriesLabel}: ${ctx.parsed.y.toLocaleString()}`,
            },
          },
        },
      },
      plugins: [highlightLabelsPlugin],
    });

    // ---------- Checkpoint chips ----------

    if (!checkpointsContainerId) return;
    const container = document.getElementById(checkpointsContainerId);
    if (!container) return;

    const chips = [];
    highlightHours.forEach((h) => {
      const idx = getClosestIndex(labels, h);
      if (idx !== null && idx !== undefined && values[idx] != null) {
        const val = values[idx];
        chips.push(
          `<span class="inline-flex items-center rounded-full bg-slate-900/80 border border-slate-700 px-2 py-[2px] mr-1 mb-1">
             <span class="text-[0.65rem] text-slate-400 mr-1">${h}h</span>
             <span class="text-[0.7rem] font-semibold text-slate-100">${Number(
               val
             ).toLocaleString()}</span>
           </span>`
        );
      }
    });

    container.innerHTML =
      chips.length > 0
        ? `<div class="text-[0.7rem] text-slate-300 flex flex-wrap items-center gap-1">
             <span class="text-slate-400 mr-1">${checkpointsLabel} checkpoints:</span>${chips.join(
               ""
             )}
           </div>`
        : `<div class="text-[0.7rem] text-slate-500">
             No checkpoints near 1h / 3h / 6h / 12h / 24h.
           </div>`;
  }

  // ---------- Shape metrics (growth distribution) ----------

  function computeShapeMetrics(labels, values) {
    const buckets = [
      { label: "0–3h", min: 0, max: 3, sum: 0 },
      { label: "3–6h", min: 3, max: 6, sum: 0 },
      { label: "6–12h", min: 6, max: 12, sum: 0 },
      { label: "12–24h", min: 12, max: 24, sum: 0 },
    ];

    for (let i = 1; i < labels.length; i++) {
      const h = labels[i];
      const delta =
        (values[i] != null ? values[i] : 0) -
        (values[i - 1] != null ? values[i - 1] : 0);
      if (delta <= 0) continue;

      for (const b of buckets) {
        if (h > b.min && h <= b.max) {
          b.sum += delta;
          break;
        }
      }
    }

    const total =
      values.length && values[values.length - 1] != null
        ? values[values.length - 1]
        : 0;

    buckets.forEach((b) => {
      b.share = total > 0 ? b.sum / total : 0;
    });

    const early = total > 0 ? (buckets[0].sum + buckets[1].sum) / total : 0;
    const late = total > 0 ? buckets[3].sum / total : 0;
    const mid = total > 0 ? buckets[2].sum / total : 0;

    return { buckets, total, early, mid, late };
  }

  // ---------- Render insights cards ----------

  function renderInsights(viralStatusLabel, behaviorLabel, metrics) {
    const shapeBody = document.getElementById("shape-analysis-body");
    const shapeTags = document.getElementById("shape-analysis-tags");
    const modelBody = document.getElementById("model-view-body");
    const actionBody = document.getElementById("action-suggestions-body");

    // Nothing to render
    if (!shapeBody && !modelBody && !actionBody) return;

    // Always provide a fallback message (even if total = 0)
    const total = metrics.total || 0;
    const earlyPct = Math.round((metrics.early || 0) * 100);
    const latePct = Math.round((metrics.late || 0) * 100);

    // --- Shape analysis text ---
    if (shapeBody) {
      let txt;
      if (total <= 0) {
        txt =
          "Not enough data yet to analyse the growth pattern. Keep tracking this video for a few more hours.";
      } else if (metrics.early >= 0.6) {
        txt =
          `About ${earlyPct}% of the first 24h views came in the first 6 hours, ` +
          "which looks like an early-push, launch-dependent pattern.";
      } else if (metrics.late >= 0.4) {
        txt =
          `A large share (~${latePct}%) of views arrived after 12h, ` +
          "indicating a late-surge, search-driven pattern.";
      } else {
        txt =
          "Views are spread relatively evenly across the first 24 hours, suggesting a steady, organic climb.";
      }
      shapeBody.textContent = txt;
    }

    // --- Shape tags (bucket shares) ---
    if (shapeTags && metrics.buckets) {
      const chips = metrics.buckets.map((b) => {
        const pct = Math.round((b.share || 0) * 100);
        return `<span class="inline-flex items-center rounded-full bg-slate-900/80 border border-slate-700 px-2 py-[2px] text-[0.7rem]">
                  <span class="text-slate-400 mr-1">${b.label}</span>
                  <span class="text-slate-100 font-semibold">${pct}%</span>
                </span>`;
      });
      shapeTags.innerHTML = chips.join("");
    }

    // --- Model explanation block ---
    if (modelBody) {
      const vs = viralStatusLabel || "N/A";
      const beh = behaviorLabel || "N/A";
      const vsLower = vs.toLowerCase();
      const behLower = beh.toLowerCase();

      const lines = [];

      lines.push(
        `Final label: <span class="font-semibold text-fuchsia-200">${vs}</span> · ` +
          `Behavior: <span class="font-semibold text-sky-200">${beh}</span>.`
      );

      if (total > 0) {
        lines.push(
          `24h views ≈ <span class="font-semibold">${total.toLocaleString()}</span>, ` +
            `<span class="font-semibold">${earlyPct}%</span> in the first 6h and ` +
            `<span class="font-semibold">${latePct}%</span> after 12h.`
        );
      }

      if (vsLower.includes("explosive") || vsLower.includes("super")) {
        lines.push(
          "The model sees both strong early growth and good continuation, which matches an 'explosive' viral pattern."
        );
      } else if (vsLower.includes("viral")) {
        lines.push(
          "Signals are above-average for this cohort, but not as extreme as the top super-viral cases."
        );
      } else if (vsLower.includes("weak")) {
        lines.push(
          "Early performance is modest. The video shows some traction but remains close to baseline."
        );
      } else {
        lines.push(
          "Growth signals look similar to a typical baseline video in this niche."
        );
      }

      if (behLower.includes("stable") || behLower.includes("consistent")) {
        lines.push(
          "Behavior is consistent across checkpoints, which usually makes this video more predictable and easier to scale."
        );
      } else if (behLower.includes("volatile")) {
        lines.push(
          "The pattern is volatile across time windows, so actual performance may swing more than the final label suggests."
        );
      } else if (behLower.includes("late")) {
        lines.push(
          "A significant portion of growth happens later, which is typical of search / recommendation tail traffic."
        );
      }

      modelBody.innerHTML =
        "<ul class='list-disc pl-4 space-y-1'>" +
        lines.map((l) => `<li>${l}</li>`).join("") +
        "</ul>";
    }

    // --- Action suggestions block ---
    if (actionBody) {
      const vsLower = (viralStatusLabel || "").toLowerCase();
      const behLower = (behaviorLabel || "").toLowerCase();
      const actions = [];

      if (total === 0) {
        actions.push(
          "Keep tracking this video for a few more hours before making any decision."
        );
      } else {
        if (vsLower.includes("explosive") || vsLower.includes("super")) {
          actions.push(
            "Highlight this video on the channel home, playlists and community posts while momentum is strong."
          );
          if (latePct < 30) {
            actions.push(
              "Consider testing paid promotion or extra cross-posting in the next 6–12 hours to extend the current surge."
            );
          }
        } else if (vsLower.includes("viral")) {
          actions.push(
            "Treat this as a solid performer: keep it pinned in relevant playlists and reuse what worked in title/thumbnail."
          );
        } else if (vsLower.includes("weak")) {
          actions.push(
            "Use this as a learning example: review hook and thumbnail for the first 30–60 seconds and compare with stronger videos."
          );
        } else {
          actions.push(
            "Focus promotion efforts on stronger candidates; keep this video discoverable but avoid heavy boosting."
          );
        }

        if (metrics.early >= 0.6) {
          actions.push(
            "Because most views arrived in the first 6h, future experiments should focus on launch strategy (CTR, notifications, initial push)."
          );
        } else if (metrics.late >= 0.4) {
          actions.push(
            "Since a large share of views comes after 12h, optimise search keywords, tags and playlists for long-tail discovery."
          );
        }
      }

      actionBody.innerHTML =
        "<ul class='list-disc pl-4 space-y-1'>" +
        actions.map((a) => `<li>${a}</li>`).join("") +
        "</ul>";
    }
  }

  // ---------- Build charts + render insights ----------

  const highlightIndexSet = getHighlightIndexSet(labels, highlightHours);

  createLineChart({
    canvasId: "timeChartViews",
    seriesLabel: "Views",
    values: views,
    baseColor: "rgba(248, 250, 252, 0.9)",
    fillColor: "rgba(248, 250, 252, 0.1)",
    checkpointsContainerId: "views-checkpoints",
    checkpointsLabel: "Views",
    labels,
    highlightIndexSet,
  });

  createLineChart({
    canvasId: "timeChartLikes",
    seriesLabel: "Likes",
    values: likes,
    baseColor: "rgba(56, 189, 248, 0.9)",
    fillColor: "rgba(56, 189, 248, 0.15)",
    checkpointsContainerId: "likes-checkpoints",
    checkpointsLabel: "Likes",
    labels,
    highlightIndexSet,
  });

  createLineChart({
    canvasId: "timeChartComments",
    seriesLabel: "Comments",
    values: comments,
    baseColor: "rgba(244, 114, 182, 0.9)",
    fillColor: "rgba(244, 114, 182, 0.15)",
    checkpointsContainerId: "comments-checkpoints",
    checkpointsLabel: "Comments",
    labels,
    highlightIndexSet,
  });

  const metrics = computeShapeMetrics(labels, views);
  renderInsights(viralStatusLabel, behaviorLabel, metrics);
});
