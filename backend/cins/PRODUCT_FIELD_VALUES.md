# Field Values

**portfolioBusiness** – Market Deal, Special Deal, Declined Deal, Partner Specific Special Deal, Registration Deal

**fuelType** – Petrol, Diesel, CNG, LPG, Electric

**dealParameter** – OD, Net, Regular TP, TP with Vehicle Age, Flat Amount, APE

**state** – Andaman & Nicobar, Andhra Pradesh, Arunachal Pradesh, Assam, Bihar, Chandigarh, Chattisgarh, Delhi NCR, Goa, Gujarat, Haryana, Jammu & Kashmir, Himachal Pradesh, Karnataka, Jharkhand, Kerala, Lakshadweep, Madhya Pradesh, Mizoram, Maharashtra, Meghalaya, Manipur, Nagaland, Orissa, Pondicherry, Rajasthan, Punjab, Sikkim, Tamil Nadu, Telangana, Tripura, West Bengal, Uttar Pradesh, Ladakh, Uttarakhand

**numberOfWheels** – 3W, 4W & Above

**businessType** – New, Renewal, Rollover, Port

**dealBusinessType** – Net, APE, TP, OD

**gvwRange** – 1-2500, 2501-3500, 3501-7500, 7501-12000, 12001-16000, 16001-40000, 40001-999999

**subproduct** – GCV, PCV, Buses, MISC-D

**fileType** – IRDA Statement, Rewards Statement, Enhancement Statement, ORC Statement

**schoolBusType** – On Individual Name, On School Name, On Company Name, On Contract (Transporter)

**productMode** – Online, Offline

**vehicleSubClass** – Private Car, Taxi, Scooter, Moped, Bike, E Rickshaw, Auto Rickshaw, Pick Up/Delivery Van, Truck, Tractor, Tipper, Heavy Truck/Semi Trailer, Dumper, Tanker, Trailer, Refrigerated Van, Bulker, School Bus, Passenger Bus, Agricultural Tractor, Backhoe Loader, Crane, Ambulance, Loader, Tractor Loader, Harvester, Excavators, Garbage Van, Motor Grader, Transit Mixer, Fork Lift Trucks, Generator Van, Drilling Rigs, Compressors, Other Carts, Plane Loaders And Other, Road Rollers, Road Sweeper, Staff Bus, Car Carrier, Greater Than Six Seater, Kaali Peeli Taxi, Not Available, E-Bike, Flat Bed Trailer, Kisan Suvidha, Container, Towing Van, Bulldozer, Compactor, Gas Tanker, Jetting Machine, Fire Vehicles

**vehicleType** – Car/Taxi, Two Wheeler, GCV, PCV, Buses, MISC-D

**product** – Car, Two Wheeler, Commercial Vehicle

**dealType** – Market Deal, Market Bonus Deal, Special Deal, Special Bonus Deal, Declined Deal, Basic Deal

**productSubproduct** – Car, Two Wheeler, Commercial - GCV, Commercial - PCV, Commercial - Buses, Commercial - MISCD

**vehicleCarrier** – Public, Private, Commercial

**vehicleAge** – 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35

**ncbStartRange / ncbEndRange** – 0-100 (inclusive percentage band). Parsed from the
grid's verbatim label: `NCB GT OR Equal to 25%` → 25-100, `NCB GT 25%` → 26-100 (strict),
`NCB 25%-45%` → 25-45, `NCB UPTO 20%` → 0-20, `NCB 25% AND ABOVE` → 25-100. A marked
tier's paired base grid gets the complement (0 to lowest-marked-start minus 1). Both null
means the rule has no NCB dimension — NOT 0-100. The legacy `NCB` Yes/No column is derived
from this band and retained for backward compatibility.

**buParameters** – Motor

**zone** – North, South, East, West

**policyType** – Third Party, Comprehensive, SAOD

**cpa** – With CPA, Without CPA

**term** – 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10

**declineDeal** – Decline

**approvalType** – Deal, Booking, Manual Override

**bookingMode** – Online, Offline

**zeroDep** – Yes, No

terminology description: A deal is commission rule being identified

For any confustion, ask question, don't assume.

refer to @backend/cins/PRODUCT_FIELD_MAPPING.md for required condition fields in final output
