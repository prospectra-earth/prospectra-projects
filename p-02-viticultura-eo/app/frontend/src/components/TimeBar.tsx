import { useEffect, useRef, useState } from "react";
import type { DateOut } from "../api/types";
import { doyToLabel } from "../domains";

interface Props {
  dates: DateOut[];
  obsDate: string | undefined;
  season: number | undefined;
  onDateChange: (date: string) => void;
}

const PlayIcon = () => (
  <svg viewBox="0 0 20 20" fill="currentColor">
    <path d="M6 4 L16 10 L6 16 Z" />
  </svg>
);
const PauseIcon = () => (
  <svg viewBox="0 0 20 20" fill="currentColor">
    <rect x="5" y="4" width="4" height="12" rx="1" />
    <rect x="11" y="4" width="4" height="12" rx="1" />
  </svg>
);

export default function TimeBar({ dates, obsDate, season, onDateChange }: Props) {
  const [playing, setPlaying] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const dateIndex = Math.max(
    0,
    dates.findIndex((d) => d.obs_date === obsDate),
  );
  const maxIndex = Math.max(0, dates.length - 1);
  const current = dates[dateIndex];

  useEffect(() => {
    if (!playing) return;
    timerRef.current = setInterval(() => {
      if (!dates.length) return;
      const idx = dates.findIndex((d) => d.obs_date === obsDate);
      const next = dates[(idx + 1) % dates.length];
      onDateChange(next.obs_date);
    }, 650);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, obsDate, dates]);

  const togglePlay = () => setPlaying((p) => !p);

  return (
    <div className="timebar">
      <button className="playbtn" onClick={togglePlay} disabled={dates.length < 2}>
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <div className="dateline">
        <div className="ticks">
          {dates.map((d, i) => (
            <div key={d.obs_date} className="tick" style={{ left: `${(i / (maxIndex || 1)) * 100}%` }} />
          ))}
        </div>
        <input
          className="slider"
          type="range"
          min={0}
          max={maxIndex}
          step={1}
          value={dateIndex}
          disabled={dates.length === 0}
          onChange={(e) => {
            const idx = parseInt(e.target.value, 10);
            const d = dates[idx];
            if (d) onDateChange(d.obs_date);
          }}
        />
      </div>
      <div className="datelabel">
        {current ? `${doyToLabel(current.doy)} ${season ?? ""}` : "—"}
        <span className="doy">{current ? `day ${current.doy} of season ${season}` : "no dates"}</span>
      </div>
    </div>
  );
}
