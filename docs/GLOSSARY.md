# Глоссарий терминов BAS-стенда

Краткий справочник аббревиатур и терминов. Для не-специалиста, впервые
открывшего проект.

## Предметная область

| Термин | Расшифровка | Что это в нашем стенде |
|---|---|---|
| **БАС** | Беспилотная Авиационная Система | Дрон + наземная инфраструктура управления |
| **ИССГР** | Информационно-Справочная Система Географического Района | Цифровой двойник обстановки: БАС, препятствия, сенсоры как геообъекты (наш OGC REST API) |
| **НПУ** | Наземный Пункт Управления | Ground control station — пульт оператора (Web GCS) |
| **АСУ** | Автоматизированная Система Управления | Внешняя ведомственная система, читает обстановку через ИССГР API |
| **ГПИ** | ГеоПространственная Информация | Объекты с геометрией (Point/Polygon/LineString) и атрибутами |
| **ОГРСИ / OGC** | Open Geospatial Consortium | Международный стандарт гео-API; мы реализуем OGC API Features 1.0 |

## Симуляция и автопилот

| Термин | Расшифровка | Что это |
|---|---|---|
| **SITL** | Software In The Loop | ArduPilot-автопилот запущен как софт (без железа), тот же код что на Pixhawk |
| **HIL/HITL** | Hardware In The Loop | Автопилот на реальном железе в петле симуляции (у нас НЕ реализовано — нужен Pixhawk) |
| **ArduPilot / ArduCopter** | — | Open-source автопилот; ArduCopter = его multicopter-прошивка |
| **PX4** | — | Альтернативный open-source автопилот (мы используем ArduPilot) |
| **MAVLink** | Micro Air Vehicle Link | Стандартный протокол связи дрон↔GCS |
| **FDM** | Flight Dynamics Model | Модель динамики полёта; JSON-FDM = ArduPilot-протокол обмена с внешней физикой |
| **6DOF** | 6 Degrees Of Freedom | Полная физика тела: 3 позиции + 3 ориентации |
| **EKF** | Extended Kalman Filter | Фильтр автопилота, оценивает позу из IMU/GPS; требует реалистичный sensor noise |
| **IMU** | Inertial Measurement Unit | Гироскоп + акселерометр |
| **PWM** | Pulse Width Modulation | Сигнал автопилота на моторы (1000-2000 мкс) |
| **MAVROS** | MAVLink ↔ ROS | Мост MAVLink в ROS2 экосистему |
| **GCS** | Ground Control Station | Пульт управления (наш Web GCS на :8765) |

## Радиофизика

| Термин | Расшифровка | Что это |
|---|---|---|
| **ns-3** | Network Simulator 3 | Эмулятор сети; считает loss/delay/jitter каналов |
| **Sionna RT** | NVIDIA Sionna Ray Tracing | Физический ray-trace радиополя по 3D-геометрии города |
| **RSSI** | Received Signal Strength Indicator | Уровень принимаемого сигнала (dBm) |
| **LoS / NLoS** | Line of Sight / Non-LoS | Прямая видимость / её отсутствие (тень за зданием) |
| **PDR** | Packet Delivery Ratio | Доля доставленных пакетов |
| **LoRa** | Long Range | Радиопротокол дальней связи; у нас калибровано под Semtech SX1276 |
| **PER** | Packet Error Rate | Доля ошибочных пакетов |
| **multipath** | многолучёвость | Сигнал приходит несколькими путями (отражения) |
| **AMSL** | Above Mean Sea Level | Высота над уровнем моря |
| **DEM / SRTM** | Digital Elevation Model / Shuttle Radar Topography Mission | Цифровая модель рельефа (мы тянем из AWS Terrain Tiles) |

## Визуал и данные

| Термин | Расшифровка | Что это |
|---|---|---|
| **AirSim / Cosys-AirSim** | — | UE5-симулятор фотореалистичного визуала и сенсоров (Cosys-AirSim = активный форк) |
| **UE5** | Unreal Engine 5 | Игровой движок для рендеринга AirSim-сцен |
| **FPV** | First Person View | Вид с борта (камера дрона) |
| **CV** | Computer Vision | Компьютерное зрение; YOLOv8-детектор |
| **YOLO** | You Only Look Once | Семейство real-time детекторов объектов |
| **Gazebo** | — | Open-source симулятор физики робототехники |
| **OSM** | OpenStreetMap | Открытая карта мира; источник зданий для сценариев |
| **OptiX** | NVIDIA OptiX | GPU ray-tracing SDK (нужен Sionna live mode) |

## Лицензии данных

| Термин | Что это |
|---|---|
| **ODbL** | Open Database License — лицензия OpenStreetMap, требует атрибуцию |
| **CC-BY / CC0** | Creative Commons (с атрибуцией / public domain) |
| **AGPL** | Affero GPL — требует раскрытия исходников при network-deployment (касается YOLOv8/ultralytics) |
| **MIT / BSD / Apache-2.0** | Permissive — бизнес-friendly |

## Где что искать

- Что за модуль и с чем связан → `docs/MODULE_MAP.md`
- Готовые цепочки модулей → `docs/SCENARIOS.md`
- Какое демо запустить → `scripts/demo.sh` или `docs/DEMOS.md`
- Что реально, что заглушка → `docs/LIMITATIONS.md`
- Сайты в браузере → `docs/ui_guide.md`
