def scheduler_thread():
    schedule.every().day.at("06:00").do(lambda: create_and_send_report("daily"))
    schedule.every().monday.at("09:00").do(lambda: create_and_send_report("weekly"))
    while True:
        schedule.run_pending()
        time.sleep(30)

