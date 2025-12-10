// dashboard.js

document.addEventListener("DOMContentLoaded", () => {

    const ctx = document.getElementById("viralChart");

    const dataViral = parseInt(ctx.dataset.viral);
    const dataNonViral = parseInt(ctx.dataset.nonviral);
    const dataUnknown = parseInt(ctx.dataset.unknown);

    new Chart(ctx, {
        type: "doughnut",
        data: {
            labels: ["Viral", "Non-viral", "Unknown"],
            datasets: [{
                data: [dataViral, dataNonViral, dataUnknown],
                borderWidth: 0,
                backgroundColor: [
                    "rgba(244, 114, 182, 0.9)",
                    "rgba(148, 163, 184, 0.9)",
                    "rgba(56, 189, 248, 0.9)"
                ]
            }]
        },
        options: {
            responsive: true,
            cutout: "60%",
            plugins: {
                legend: {
                    labels: {
                        color: "#cbd5e1",
                        font: { size: 11 }
                    }
                }
            }
        }
    });
});
