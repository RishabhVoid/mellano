import type { ResponseUnit } from './types';

export interface TrafficCondition {
  congestion: number; // 0.0 (empty road) to 1.0 (deadlock gridlock)
  speedKmh: number; // Simulated realistic instantaneous speed
  freeFlowSpeedKmh: number;
  delayMultiplier: number;
  label: string; // Brief traffic badge, e.g. "Congestion 78% · 24 km/h"
  description: string; // Full telemetry context
  chokePoint: string; // Specific Delhi choke point name
}

// Known Delhi traffic corridors mapped to local metres [x, z] (origin: [714880, 3179180])
const CHOKE_POINTS: { name: string; x: number; z: number; radius: number; severity: number }[] = [
  { name: 'ITO Junction & Vikas Marg Bridge', x: 6700, z: 7800, radius: 1400, severity: 0.88 },
  { name: 'Old Delhi / Chandni Chowk Corridors', x: 5300, z: 6400, radius: 1600, severity: 0.94 },
  { name: 'Connaught Place Radial Arterials', x: 4700, z: 8100, radius: 1200, severity: 0.78 },
  { name: 'Delhi Gate / Daryaganj Choke Point', x: 5600, z: 7200, radius: 900, severity: 0.86 },
  { name: 'Laxmi Nagar / Shakarpur East Corridor', x: 8400, z: 7600, radius: 1500, severity: 0.82 },
  { name: 'Kashmere Gate ISBT & Yamuna Bypass', x: 5800, z: 4900, radius: 1300, severity: 0.85 },
  { name: 'Nizamuddin / Sarai Kale Khan Hub', x: 7400, z: 11200, radius: 1400, severity: 0.76 },
  { name: 'Civil Lines / North Delhi Connector', x: 4900, z: 3800, radius: 1100, severity: 0.58 },
];

/**
 * Calculates a realistic dynamic Delhi traffic condition for any position and vehicle.
 * Accounts for Delhi spatial congestion choke points, intersection wave pulses,
 * emergency vehicle siren priority clearance, and water hydrodynamics for rescue boats.
 */
export function calculateDelhiTraffic(
  position: [number, number],
  time_s: number,
  vehicleType: ResponseUnit['type'] = 'ambulance'
): TrafficCondition {
  const [x, z] = position;

  // 1. Watercraft: Hydrodynamic flow, river channel fairway vs bank shallows & debris
  if (vehicleType === 'rescue_boat') {
    // Yamuna river channel runs roughly north-to-south around x = 6500..7400
    const riverCentreX = 6900 + Math.sin(z * 0.0006) * 450;
    const distToFairway = Math.abs(x - riverCentreX);
    // Fairway has deep free water (24-28 km/h), margins have debris/shallows (8-14 km/h)
    const waterResistance = Math.min(1, distToFairway / 800);
    const flowWave = Math.sin(time_s * 0.15 + z * 0.003) * 0.12;
    const congestion = Math.max(0.1, Math.min(0.9, 0.22 + waterResistance * 0.55 + flowWave));

    const freeFlowSpeedKmh = 28;
    const speedKmh = Math.max(7, Math.round(freeFlowSpeedKmh * (1 - congestion * 0.65)));
    const chokePoint = distToFairway < 350 ? 'Yamuna Central River Channel' : 'Yamuna Bank / Submerged Shallows';
    const label = `Water flow · ${speedKmh} km/h`;
    const description = distToFairway < 350
      ? `River fairway cruising (${speedKmh} km/h · 1.8m draft clearance)`
      : `Shallow water navigation (${speedKmh} km/h · debris resistance)`;

    return { congestion, speedKmh, freeFlowSpeedKmh, delayMultiplier: speedKmh / freeFlowSpeedKmh, label, description, chokePoint };
  }

  // 2. Road vehicles: Realistic Delhi urban traffic model
  let maxChokeCongestion = 0.28; // Baseline free-flowing arterial background
  let nearestChoke = 'Delhi Arterial Network';
  let bestDist = Infinity;

  for (const choke of CHOKE_POINTS) {
    const d = Math.hypot(x - choke.x, z - choke.z);
    if (d < choke.radius) {
      const weight = 1 - d / choke.radius;
      const localCongestion = choke.severity * weight;
      if (localCongestion > maxChokeCongestion) {
        maxChokeCongestion = localCongestion;
        nearestChoke = choke.name;
        bestDist = d;
      }
    }
  }

  // Add dynamic traffic shockwave pulse (simulating traffic signals and stop-and-go every 300m)
  const wavePulse = 0.11 * Math.sin(time_s * 0.09 + x * 0.004 + z * 0.003);
  const intersectionFriction = 0.08 * Math.sin(x * 0.021) * Math.cos(z * 0.021);
  const rawCongestion = Math.max(0.12, Math.min(0.98, maxChokeCongestion + wavePulse + intersectionFriction));

  // Emergency vehicle priority and siren clearing:
  // Ambulances & police sirens part traffic, granting a clearance bonus through gridlock
  let freeFlowSpeed = 65;
  let sirenClearance = 0.24; // +24% speed push through congestion
  let vehicleName = 'Ambulance';

  if (vehicleType === 'fire_engine') {
    freeFlowSpeed = 52; // Heavy 18-ton vehicle, slower turning radius
    sirenClearance = 0.16;
    vehicleName = 'Fire Engine';
  } else if (vehicleType === 'police') {
    freeFlowSpeed = 74; // Fast patrol cruiser
    sirenClearance = 0.28;
    vehicleName = 'Police Escort';
  }

  // Congestion slows vehicle down: in dead gridlock (0.95), speed drops to ~16-24 km/h with sirens
  const effectiveFriction = Math.max(0.15, rawCongestion * (1 - sirenClearance));
  const speedKmh = Math.max(14, Math.round(freeFlowSpeed * (1 - effectiveFriction * 0.72)));
  const delayMultiplier = speedKmh / freeFlowSpeed;
  const congestionPercent = Math.round(rawCongestion * 100);

  const label = `Traffic ${congestionPercent}% · ${speedKmh} km/h`;
  let trafficSeverityLabel = 'Moderate arterial traffic';
  if (rawCongestion > 0.82) trafficSeverityLabel = 'Severe Delhi gridlock';
  else if (rawCongestion > 0.65) trafficSeverityLabel = 'Heavy corridor congestion';
  else if (rawCongestion > 0.45) trafficSeverityLabel = 'Moderate traffic flow';
  else trafficSeverityLabel = 'Light traffic / open flyover';

  const description = `${vehicleName} · ${speedKmh} km/h (${trafficSeverityLabel} near ${nearestChoke} · sirens active)`;

  return {
    congestion: rawCongestion,
    speedKmh,
    freeFlowSpeedKmh: freeFlowSpeed,
    delayMultiplier,
    label,
    description,
    chokePoint: nearestChoke,
  };
}
