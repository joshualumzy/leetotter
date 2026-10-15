export type OtterPose = "float" | "doze" | "confused" | "shell";

// First days from the approved ticket #39 prototype's Lunar New Year calendar table.
const lunarNewYear: Record<number, [number, number]> = {
  2026: [2, 17], 2027: [2, 6], 2028: [1, 26], 2029: [2, 13], 2030: [2, 3],
};

export function touchForDate(date: Date): string {
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const first = lunarNewYear[year];
  if (first) {
    const firstDay = new Date(year, first[0] - 1, first[1]);
    const lastDay = new Date(year, first[0] - 1, first[1] + 3);
    if (date >= firstDay && date < lastDay) return "lny";
  }
  if (month === 12 && day >= 20 && day <= 26) return "xmas";
  if (month === 10 && day >= 25 && day <= 31) return "halloween";
  const hour = date.getHours();
  const time = hour >= 23 || hour < 5 ? "night" :
    hour >= 5 && hour < 11 ? "morning" :
    hour >= 12 && hour < 18 ? "afternoon" : "";
  const weekend = date.getDay() === 0 || date.getDay() === 6 ? "weekend" : "";
  return [time, weekend].filter(Boolean).join(" ");
}

export function otter(pose: OtterPose): string {
  return `<svg class="otter pose-${pose}" viewBox="0 0 100 100" aria-hidden="true">
    <g class="water back"><path d="M0 81 Q13 77 26 81 T52 81 T78 81 T104 81 V100 H0Z"/></g>
    <g class="bob">
      <ellipse class="fur" cx="50" cy="86" rx="24" ry="20"/>
      <g class="head">
        <circle class="ear" cx="30" cy="32" r="6.5"/><circle class="ear-inner" cx="30" cy="32" r="3"/>
        <circle class="ear" cx="70" cy="32" r="6.5"/><circle class="ear-inner" cx="70" cy="32" r="3"/>
        <ellipse class="fur" cx="50" cy="48" rx="25" ry="22"/>
        <ellipse class="face" cx="50" cy="53" rx="19.5" ry="15"/>
        <g class="look"><g class="eyes"><circle cx="41" cy="48" r="3.4"/><circle cx="59" cy="48" r="3.4"/><circle class="glint" cx="42.2" cy="46.8" r="1.1"/><circle class="glint" cx="60.2" cy="46.8" r="1.1"/></g></g>
        <g class="shut"><path d="M37.5 48.5q3.5 2.8 7 0M55.5 48.5q3.5 2.8 7 0"/></g>
        <path class="brow" d="M55 40.5l8.5-2.2"/>
        <circle class="blush" cx="35" cy="56" r="3.6"/><circle class="blush" cx="65" cy="56" r="3.6"/>
        <ellipse class="nose" cx="50" cy="54.5" rx="4.3" ry="3"/>
        <path class="mouth" d="M46 58.5q2 2.6 4 0q2 2.6 4 0"/>
        <path class="whisk" d="M39 57.5l-10-1.8M39 60l-9.5 1.5M61 57.5l10-1.8M61 60l9.5 1.5"/>
        <g class="hat hat-night"><path d="M29 33Q50 19 71 31Q75 20 88 21Q81 12 66 12Q45 11 29 33Z" fill="#4a5fae"/><circle cx="48" cy="20" r="1" fill="#ffd34d"/><circle cx="62" cy="16" r=".9" fill="#ffd34d"/><path d="M28.5 33Q50 21 71.5 31" stroke="#fff" stroke-width="4" fill="none"/><circle cx="88" cy="21" r="4" fill="#fff"/></g>
        <g class="hat hat-xmas"><path d="M29 33Q50 19 71 31Q75 20 88 21Q81 12 66 12Q45 11 29 33Z" fill="#d63b3b"/><path d="M28.5 33Q50 21 71.5 31" stroke="#fff" stroke-width="4.5" fill="none"/><circle cx="88" cy="21" r="4" fill="#fff"/></g>
        <g class="hat hat-halloween"><ellipse cx="50" cy="28" rx="26" ry="4.5" fill="#3d2a5c"/><path d="M37 28L54 3 58 6 63 28Z" fill="#3d2a5c"/><path d="M37.5 24.5h25.5v3.5h-25.5z" fill="#f28c28"/></g>
        <g class="hat hat-afternoon"><rect x="35.5" y="31" width="12" height="7.5" rx="3.4" fill="#2b2f36"/><rect x="52.5" y="31" width="12" height="7.5" rx="3.4" fill="#2b2f36"/><path d="M47.5 34h5" stroke="#2b2f36" stroke-width="1.6"/></g>
        <g class="hat hat-weekend"><g fill="#ff7aa2"><circle cx="74" cy="34.5" r="2.7"/><circle cx="77.4" cy="37" r="2.7"/><circle cx="76.2" cy="41" r="2.7"/><circle cx="71.8" cy="41" r="2.7"/><circle cx="70.6" cy="37" r="2.7"/></g><circle cx="74" cy="38.2" r="1.7" fill="#ffd34d"/></g>
        <g class="hat hat-lny"><path d="M25 27l-8-5.5v11zM25 27l8-5.5v11z" fill="#d6262f"/><circle cx="25" cy="27" r="2.8" fill="#f2b53a"/></g>
      </g>
      <ellipse class="paw" cx="40" cy="72" rx="5.5" ry="4.5"/><ellipse class="paw" cx="60" cy="72" rx="5.5" ry="4.5"/>
      <g class="shell"><path d="M44 77q-3-10 4-15l7 13-5 6zM56 77q3-10-4-15l-7 13 5 6z"/><path class="shell-crack" d="M50 65l-2 4 3 3-2 5"/></g>
      <g class="zzz"><text x="70" y="30">z</text><text x="77" y="22">z</text><text x="84" y="13">Z</text></g>
      <text class="question" x="76" y="30">?</text>
      <circle class="drop" cx="75" cy="40" r="2.2"/>
      <g class="hat hat-morning"><path class="steam" d="M83 66q-2-3 0-6t0-6M88 66q-2-3 0-6t0-6" fill="none" stroke="#b9c4cc" stroke-width="1.2"/><path d="M79 69h13l-1.6 10h-9.8z" fill="#fff" stroke="#c9b9a6" stroke-width=".8"/><ellipse cx="85.5" cy="69.4" rx="6.2" ry="1.4" fill="#7a4a2a"/><path d="M92 71.5q3.4 0 3 3t-3.6 2.6" fill="none" stroke="#c9b9a6" stroke-width="1.2"/></g>
      <g class="yay"><path d="M18 30l1.2 3 3 1.2-3 1.2-1.2 3-1.2-3-3-1.2 3-1.2zM82 26l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z"/></g>
    </g>
    <g class="water front"><path d="M0 85 Q13 81 26 85 T52 85 T78 85 T104 85 V100 H0Z"/></g>
  </svg>`;
}
