# BMWET Fuel Prices

This project publishes machine-readable versions of fuel and heating-oil
prices published by the Austrian Federal Ministry for Economic Affairs,
Energy and Tourism (BMWET).

## Data Source

The source is the official BMWET page:

[Current energy prices](https://www.bmwet.gv.at/Themen/Energie/kosten.html)

The project reads the published HTML table and republishes its values as
versioned JSON and CSV data. It is not an official BMWET API.

The published values are weighted averages including taxes and charges. They
should not be interpreted as individual supplier prices.

## Published Data

The GitHub Pages site and versioned data endpoints are available here:

- [Project website](https://zapster.github.io/bmwet-treibstoffpreise/)
- [Latest prices (JSON)](https://zapster.github.io/bmwet-treibstoffpreise/api/v1/latest.json)
- [Prices and history (JSON)](https://zapster.github.io/bmwet-treibstoffpreise/api/v1/prices.json)
- [Prices and history (CSV)](https://zapster.github.io/bmwet-treibstoffpreise/api/v1/prices.csv)

## Status

The local scraper, history merge, validation, and offline tests are implemented.
The GitHub Pages homepage and deployment workflow are implemented. Automated
fetching and scheduling are not implemented yet. Historical data from before
2026 is planned but not implemented yet.

## License

The repository code is licensed under the MIT License. The generated data is
derived from BMWET publications and remains subject to the applicable source
terms.
