# 🎓 Smart Education Bot (NZ.ua & Human.ua)

A high-performance asynchronous Telegram bot serving **1000+ active students**. It integrates complex web scraping, REST
API aggregation, and generative AI to automate educational tracking and provide real-time analytics.

## 🚀 Technical Highlights
    
* **Advanced Web Scraping**: Implements `cloudscraper` and `BeautifulSoup4` to bypass Cloudflare protection and extract
  DOM data from legacy educational platforms without open APIs.
* **REST API Integration**: Reverse-engineered and integrated the private API of Human.ua, handling dynamic JWT/Session
  tokens and parsing deeply nested JSON/XML payloads.
* **Asynchronous Task Queue**: Custom background scheduler (`asyncio`) handling real-time push notifications for lesson
  starts and grade updates with strict rate-limiting (Semaphore).
* **Multimodal AI Assistant**: Integrated Google Gemini API for personalized homework tutoring, featuring on-the-fly
  image compression (PIL) to optimize payload size and token cost.
* **Secure Data Management**: User credentials (passwords) are strictly encrypted at rest using AES
  cryptography (`Fernet`).

## 🛠 Tech Stack

* **Core**: Python 3.13, Aiogram 3.x, Asyncio
* **Data Extraction**: Cloudscraper, Requests, BeautifulSoup4
* **Database**: SQLite3 (Optimized for concurrent async access)
* **Media & Analytics**: Pillow (PIL) for dynamic Wrapped generation, QuickChart for admin dashboard rendering.
* **Security**: Cryptography (Fernet)

## 📊 Business Logic Features

* **VIP Monetization Engine**: Automated payment processing via Telegram Stars (`XTR`) and manual verification flows.
* **Referral System**: Built-in viral loop granting temporary VIP access for inviting new active users.
* **Smart Memory Management**: Implemented targeted caching and scheduled Garbage Collection (`gc.collect()`) to
  maintain stability on low-RAM VPS instances.
