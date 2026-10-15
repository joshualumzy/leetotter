export type OtterPose = "float" | "doze" | "confused";

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
        <g class="eyes"><circle cx="41" cy="48" r="3.4"/><circle cx="59" cy="48" r="3.4"/><circle class="glint" cx="42.2" cy="46.8" r="1.1"/><circle class="glint" cx="60.2" cy="46.8" r="1.1"/></g>
        <g class="shut"><path d="M37.5 48.5q3.5 2.8 7 0M55.5 48.5q3.5 2.8 7 0"/></g>
        <path class="brow" d="M55 40.5l8.5-2.2"/>
        <circle class="blush" cx="35" cy="56" r="3.6"/><circle class="blush" cx="65" cy="56" r="3.6"/>
        <ellipse class="nose" cx="50" cy="54.5" rx="4.3" ry="3"/>
        <path class="mouth" d="M46 58.5q2 2.6 4 0q2 2.6 4 0"/>
        <path class="whisk" d="M39 57.5l-10-1.8M39 60l-9.5 1.5M61 57.5l10-1.8M61 60l9.5 1.5"/>
      </g>
      <ellipse class="paw" cx="40" cy="72" rx="5.5" ry="4.5"/><ellipse class="paw" cx="60" cy="72" rx="5.5" ry="4.5"/>
      <g class="zzz"><text x="70" y="30">z</text><text x="77" y="22">z</text><text x="84" y="13">Z</text></g>
      <text class="question" x="76" y="30">?</text>
    </g>
    <g class="water front"><path d="M0 85 Q13 81 26 85 T52 85 T78 85 T104 85 V100 H0Z"/></g>
  </svg>`;
}
