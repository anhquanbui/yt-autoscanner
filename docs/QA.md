# Q&A: Personal Reflections and Strategic Focus — yt-autoscanner (v7.x)

---

**What are the key things I care about?**  
I care about **efficient training data**, **cost-effective model training**, and a **clean, well-structured, automated system**.

---

**Which collections are the main ones in the system?**  
The core collections are `videos` and `processed_videos`. Everything else — such as `channels`, `dashboard_summary`, or temporary exports — serves supporting roles. These auxiliary collections exist mainly to enhance performance, visualization, or data enrichment, but the true analytical and modeling backbone lies in `videos` and `processed_videos`.

---

**Why separate `videos` vs `processed_videos`?**  
They serve different purposes — `videos` stores raw tracking data, while `processed_videos` holds analytics-ready features and labels for machine learning.

---

**Why do I store so much data, and why is my database heavy?**  
Because every stage of the system depends on rich historical context. I keep detailed snapshots and tracking logs so that models can analyze growth patterns, verify trends, and perform backtesting. This naturally makes the MongoDB instance large — storing time-series data for thousands of videos, multiple metrics per snapshot, and derived fields. The weight comes from design: accuracy and reproducibility require complete data history, even if it costs more storage.

---

**Why did I build multiple workers and tools, such as `mongo_to_parquet`?**  
To keep the system modular, efficient, and scalable. Each worker or tool has a focused responsibility — for example, `mongo_to_parquet` converts database collections into lightweight, columnar files for faster analytics and model training. This modular design allows parallel execution, easier debugging, and flexibility to scale or replace components without breaking the whole pipeline. Other examples include `make_indexes.py`, which ensures MongoDB collections are optimized for query speed and prevents full-scan operations, and `backfill_channels.py`, which updates or populates missing channel metadata to keep joins consistent and analytics accurate.

---

**How does the pipeline stay synchronized between workers?**
Each worker uses shared fields like status, processed_status, and timestamps to stay coordinated. For example, track_once updates tracking.status, process_data looks for completed videos only, and low_quality_predictor flags them asynchronously. This event-driven coordination prevents overlap and ensures every stage knows when to start or stop.

---

**How can new models or predictors be added safely?**
By extending the system modularly — each new model should have its own ml_flags or output field, without overwriting existing data. This guarantees backward compatibility, traceability, and easy rollback if needed. Logging and schema versioning keep integration transparent across all workers.

---

**Example: track_once and the low_quality model?**
First, track_once collects snapshots and updates the video document in videos. Then the low_quality model reads those snapshots, computes growth features, and writes its prediction into ml_flags.low_quality as 0 or 1. On the next pass, track_once scans for videos with ml_flags.low_quality = 1, sets their status = "complete", and updates stop_reason = "low_quality". This way, the model decides what to stop, and track_once decides how to stop it in the tracking pipeline. The video could be calculated at the TS of 3 - 6 hour.

---

**Can I train entirely using the `videos` collection?**  
Yes, but it’s resource-heavy. It’s technically possible to train directly from `videos`, but it consumes significant RAM and time. On Colab Free it can crash sessions, and on local machines it may freeze due to the large number of raw snapshots and heavy time-series processing. That’s exactly why `processed_videos` exists — it serves as the optimized, pre-aggregated solution for faster, safer, and more stable model training.

---

**Does every ML model need to export or save something?**  
Not always — it depends on the model’s role. Some models only need to save lightweight results such as flags or scores instead of exporting full files. For example, in my system, most predictors (like low_quality) just write updated flags back to the database, while heavier models for viral prediction may export full datasets or trained weights for later analysis.

---

**What did I use to train the low_quality model?**  
I trained it directly from the `videos` collection. The model was built with Python using `pandas`, `scikit-learn`, and `xgboost`. Despite the higher memory cost, it allowed me to work directly with the raw snapshots and growth metrics to detect low-quality videos without relying on preprocessed data. Of course, `processed_videos` could also be used, but I originally designed it for viral detection models. To train a low-quality classifier, its structure would need to be redefined. That’s why I chose to train directly on `videos` instead.

---

**What is the function of the low_quality model?**  
Its main purpose is to automatically identify and flag videos with weak growth potential within the first 24 hours. Once a video is labeled as low-quality, the system can stop tracking it early to save API quota, disk space, and processing time while focusing resources on higher-potential videos.

---

**How does the low_quality model support or affect the viral prediction model?**  
It acts as a pre-filter, cleaning out low-potential videos before they reach the viral prediction stage. By removing noisy, stagnant samples early, it helps the viral model train and infer on cleaner, more dynamic datasets. This improves accuracy, reduces imbalance between viral vs non-viral samples, and saves computational cost for high-growth analysis.

---

**Does the low_quality model export anything, or just store flags?**  
It mainly stores flags. The model doesn’t export heavy artifacts or separate files — instead, it writes predictions back into the database by updating the `ml_flags` or `label_low_quality` field inside each video document. This lightweight design avoids redundant storage and keeps the workflow simple, since model updates are immediately visible to other modules like `track_once.py`.

---