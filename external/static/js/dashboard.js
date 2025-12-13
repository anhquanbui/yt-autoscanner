// dashboard.js

document.addEventListener("DOMContentLoaded", () => {

    // Canvas element for the viral distribution chart
    const ctx = document.getElementById("viralChart");

    // Read data values from data-* attributes
    const dataViral = parseInt(ctx.dataset.viral);
    const dataNonViral = parseInt(ctx.dataset.nonviral);
    const dataUnknown = parseInt(ctx.dataset.unknown);

    // Render doughnut chart
    new Chart(ctx, {
        type: "doughnut",
        data: {
            labels: ["Viral", "Non-viral", "Unknown"],
            datasets: [{
                data: [dataViral, dataNonViral, dataUnknown],
                borderWidth: 0,
                backgroundColor: [
                    "rgba(244, 114, 182, 0.9)",   // Viral
                    "rgba(148, 163, 184, 0.9)",   // Non-viral
                    "rgba(56, 189, 248, 0.9)"     // Unknown
                ]
            }]
        },
        options: {
            responsive: true,
            cutout: "60%", // inner radius for doughnut effect
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
