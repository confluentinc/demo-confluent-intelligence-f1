#!/bin/bash
set -e

yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker

# Create init SQL directory
mkdir -p /opt/postgres-init

cat > /opt/postgres-init/01_drivers.sql << 'SQLEOF'
CREATE TABLE drivers (
  car_number INT PRIMARY KEY,
  driver VARCHAR(100) NOT NULL,
  team VARCHAR(100) NOT NULL,
  nationality VARCHAR(50) NOT NULL,
  championships INT DEFAULT 0,
  career_wins INT DEFAULT 0,
  career_podiums INT DEFAULT 0,
  season_points INT DEFAULT 0,
  season_position INT DEFAULT 0
);

INSERT INTO drivers VALUES
  (1,  'Max Eriksson',      'Titan Dynamics',    'Swedish',       4, 63, 111, 175, 1),
  (2,  'Yuki Tanaka',       'Titan Dynamics',    'Japanese',      0, 1,  8,   42,  12),
  (4,  'Luca Novak',        'Apex Motorsport',   'Czech',         1, 12, 35,  148, 2),
  (3,  'Daniel Costa',      'Apex Motorsport',   'Brazilian',     0, 3,  14,  58,  9),
  (44, 'James River',       'River Racing',      'British',       7, 105,202, 120, 3),
  (77, 'Sophie Laurent',    'River Racing',      'French',        0, 0,  5,   35,  14),
  (16, 'Carlos Vega',       'Scuderia Rossa',    'Spanish',       0, 8,  40,  98,  4),
  (55, 'Marco Rossi',       'Scuderia Rossa',    'Italian',       0, 2,  12,  52,  10),
  (63, 'Oliver Walsh',      'Sterling GP',       'British',       0, 3,  18,  85,  5),
  (12, 'Pierre Blanc',      'Sterling GP',       'French',        0, 0,  4,   30,  15),
  (14, 'Fernando Reyes',    'Aston Verde',       'Mexican',       0, 2,  15,  72,  6),
  (18, 'Kimi Lahtinen',     'Aston Verde',       'Finnish',       0, 0,  6,   38,  13),
  (10, 'Theo Martin',       'Alpine Force',      'French',        0, 1,  10,  65,  7),
  (31, 'Oscar Patel',       'Alpine Force',      'Australian',    0, 0,  3,   28,  16),
  (20, 'Kevin Andersen',    'Haas Velocity',     'Danish',        0, 0,  2,   22,  17),
  (27, 'Nico Hoffman',      'Haas Velocity',     'German',        0, 0,  1,   18,  18),
  (24, 'Valtteri Koskinen', 'Sauber Spirit',     'Finnish',       0, 1,  8,   60,  8),
  (23, 'Li Wei',            'Sauber Spirit',     'Chinese',       0, 0,  2,   25,  19),
  (6,  'Alex Nakamura',     'Williams Heritage', 'Japanese',      0, 0,  5,   45,  11),
  (8,  'Logan Mitchell',    'Williams Heritage', 'American',      0, 0,  0,   12,  20),
  (22, 'Liam O''Brien',     'Racing Bulls',      'Irish',         0, 0,  3,   20,  21),
  (21, 'Isack Mbeki',       'Racing Bulls',      'South African', 0, 0,  1,   15,  22);
SQLEOF

# Start Postgres with CDC-ready config
docker run -d \
  --name postgres \
  -p 5432:5432 \
  -e POSTGRES_DB=f1demo \
  -e POSTGRES_USER=f1user \
  -e POSTGRES_PASSWORD=f1passw0rd \
  -v /opt/postgres-init:/docker-entrypoint-initdb.d \
  postgres:15 \
  -c wal_level=logical \
  -c max_replication_slots=5 \
  -c max_wal_senders=5
