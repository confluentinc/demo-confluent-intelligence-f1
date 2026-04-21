"""Semi-scripted race state management for 22 cars across 57 laps.

Uses a CUMULATIVE RACE TIME model (like real F1):
- Each car accumulates total_race_time across laps
- Positions are determined by sorting all cars by total_race_time (lowest = P1)
- Overtakes happen naturally when a faster car's cumulative time undercuts a slower car's
- Pit stops add ~23 seconds to one lap but give fresh tires (faster subsequent laps)
"""

# Tire degradation: seconds added to lap time per lap of tire age
TIRE_DEGRADATION = {
    "SOFT": 0.12,    # Fast initially but degrades quickly — 32 laps = +3.84s
    "MEDIUM": 0.08,  # Balanced — 25 laps = +2.00s
    "HARD": 0.02,    # Slow but durable — 40 laps = +0.80s
}

# Base lap time by compound (lower = faster)
TIRE_BASE_PACE = {
    "SOFT": 90.5,
    "MEDIUM": 91.0,
    "HARD": 91.5,
}

# pace_delta is now per-driver (defined in drivers.py GRID)

PIT_STOP_TIME_SEC = 20.0


class CarState:
    def __init__(self, car_info):
        self.car_number = car_info["car_number"]
        self.driver = car_info["driver"]
        self.team = car_info["team"]
        self.position = car_info["start_position"]
        self.tire_compound = car_info["start_tire"]
        self.tire_age_laps = 0
        self.pit_stops = 0
        self.pit_lap = car_info["pit_lap"]
        self.pit_tire = car_info["pit_tire"]
        self.in_pit_lane = False
        self.gap_to_leader_sec = 0.0
        self.gap_to_ahead_sec = 0.0
        self.last_lap_time_sec = TIRE_BASE_PACE[self.tire_compound]
        self.has_pitted = False
        # Fixed per driver — represents inherent car/driver pace
        self.qualifying_delta = car_info["pace_delta"]
        # Starting gap from pole (simulates grid spread at race start)
        self.total_race_time = (car_info["start_position"] - 1) * 0.3

    def lap_time(self):
        """Calculate current lap time based on tire compound, age, and driver skill.

        Returns seconds for this lap. Does NOT include pit stop time
        (that's added separately in advance_lap).
        """
        base = TIRE_BASE_PACE[self.tire_compound]
        degradation = TIRE_DEGRADATION[self.tire_compound] * self.tire_age_laps
        return base + degradation + self.qualifying_delta

    def pit(self, new_compound):
        self.tire_compound = new_compound
        self.tire_age_laps = 0
        self.pit_stops += 1
        self.in_pit_lane = True
        self.has_pitted = True


class RaceState:
    def __init__(self, grid):
        self.cars = [CarState(d) for d in grid]
        self.current_lap = 0
        # Sort by starting position (= by total_race_time since it's proportional)
        self.cars.sort(key=lambda c: c.total_race_time)
        for i, car in enumerate(self.cars):
            car.position = i + 1
        self._update_gaps()

    def advance_lap(self):
        """Advance the race by one lap.

        1. Process pit stops for this lap
        2. Increment tire age
        3. Calculate each car's lap time and add to total_race_time
        4. Add pit stop penalty if pitting this lap
        5. Re-sort by total_race_time to get new positions
        6. Update gaps
        """
        self.current_lap += 1

        # Clear pit lane status from previous lap
        for car in self.cars:
            car.in_pit_lane = False

        # Process pit stops
        for car in self.cars:
            if self.current_lap == car.pit_lap and not car.has_pitted:
                car.pit(car.pit_tire)

        # Increment tire age and calculate lap times
        for car in self.cars:
            car.tire_age_laps += 1
            car.last_lap_time_sec = car.lap_time()

            # Add pit stop time penalty on the lap the car pits
            effective_lap_time = car.last_lap_time_sec
            if car.in_pit_lane:
                effective_lap_time += PIT_STOP_TIME_SEC

            car.total_race_time += effective_lap_time

        # Sort by cumulative race time — this determines positions
        self.cars.sort(key=lambda c: c.total_race_time)
        for i, car in enumerate(self.cars):
            car.position = i + 1

        self._update_gaps()

    def _update_gaps(self):
        """Update gap_to_leader and gap_to_ahead based on total_race_time."""
        if not self.cars:
            return
        leader_time = self.cars[0].total_race_time
        for i, car in enumerate(self.cars):
            car.gap_to_leader_sec = round(car.total_race_time - leader_time, 1)
            if i == 0:
                car.gap_to_ahead_sec = 0.0
            else:
                car.gap_to_ahead_sec = round(
                    car.total_race_time - self.cars[i - 1].total_race_time, 1
                )

    def get_car(self, car_number):
        """Get current state for a specific car."""
        for car in self.cars:
            if car.car_number == car_number:
                return {
                    "car_number": car.car_number,
                    "driver": car.driver,
                    "team": car.team,
                    "lap": self.current_lap,
                    "position": car.position,
                    "gap_to_leader_sec": car.gap_to_leader_sec,
                    "gap_to_ahead_sec": car.gap_to_ahead_sec,
                    "last_lap_time_sec": round(car.last_lap_time_sec, 3),
                    "pit_stops": car.pit_stops,
                    "tire_compound": car.tire_compound,
                    "tire_age_laps": car.tire_age_laps,
                    "in_pit_lane": car.in_pit_lane,
                }
        return None

    def get_standings(self):
        """Get all 22 cars sorted by position."""
        return [self.get_car(c.car_number) for c in self.cars]
