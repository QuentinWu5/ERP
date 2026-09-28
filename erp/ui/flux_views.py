        ttk.Label(win, text="Client").grid(row=0, column=0)
        date_e = self.app._form_fields(win, [("desired_date", "Livraison souhaitée (AAAA-MM-JJ)")],
                                       start_row=1)
        if date_e is None or not cb.get():
            return
