"""Regenerates the binary sample fixtures (pdf, sqlite) that aren't practical
to keep as hand-edited text in the repo. txt/md/csv samples are plain text and
are committed directly instead.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


def make_pdf() -> None:
    out_path = SAMPLES / "pdf" / "manual.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open()

    page1 = doc.new_page()
    y = 72
    page1.insert_text((72, y), "Equipment A Sensor Troubleshooting Manual", fontsize=20)
    y += 40
    page1.insert_text((72, y), "Overview", fontsize=15)
    y += 24
    page1.insert_text(
        (72, y),
        "This manual explains how to diagnose and clear a sensor noise",
        fontsize=11,
    )
    y += 16
    page1.insert_text(
        (72, y),
        "threshold exceedance reported by Equipment A during startup.",
        fontsize=11,
    )
    y += 40
    page1.insert_text((72, y), "Sensor Thresholds", fontsize=15)
    y += 30
    table_lines = [
        ["Sensor", "Threshold", "Unit"],
        ["Vibration", "0.5", "g"],
        ["Temperature", "45", "C"],
    ]
    for row in table_lines:
        page1.insert_text((72, y), "   ".join(row), fontsize=11)
        y += 18

    page2 = doc.new_page()
    y = 72
    page2.insert_text((72, y), "Initialization Procedure", fontsize=15)
    y += 24
    steps = [
        "1. Power on the equipment and wait for boot to finish.",
        "2. Run the sensor calibration routine from the maintenance menu.",
        "3. Confirm the noise threshold reading stabilizes before resuming",
        "   normal operation.",
    ]
    for step in steps:
        page2.insert_text((72, y), step, fontsize=11)
        y += 18

    doc.save(out_path)
    doc.close()
    print(f"wrote {out_path}")


def make_sqlite() -> None:
    out_path = SAMPLES / "sqlite" / "sample.db"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(str(out_path))
    conn.executescript(
        """
        CREATE TABLE equipment (
            equipment_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT
        );

        CREATE TABLE sensor_reading (
            reading_id INTEGER PRIMARY KEY,
            equipment_id TEXT NOT NULL,
            sensor TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (equipment_id) REFERENCES equipment(equipment_id)
        );

        INSERT INTO equipment VALUES ('equipment-a', 'Equipment A', 'Plant 1 - Line 3');
        INSERT INTO equipment VALUES ('equipment-b', 'Equipment B', 'Plant 1 - Line 4');

        INSERT INTO sensor_reading VALUES (1, 'equipment-a', 'vibration', 0.42, 'g', '2026-06-01T08:00:00');
        INSERT INTO sensor_reading VALUES (2, 'equipment-a', 'vibration', 0.61, 'g', '2026-06-01T08:01:00');
        INSERT INTO sensor_reading VALUES (3, 'equipment-a', 'temperature', 36.2, 'C', '2026-06-01T08:00:00');
        INSERT INTO sensor_reading VALUES (4, 'equipment-b', 'vibration', 0.30, 'g', '2026-06-01T08:00:00');
        """
    )
    conn.commit()
    conn.close()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    make_pdf()
    make_sqlite()
