class CurieriRomaniaPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._activeTab = this._loadPreference("active_tab") || "home";
    this._selectedEntityId = null;
    this._parcelSort = this._loadPreference("parcel_sort") || "newest";
    this._parcelSearch = "";
    this._licenseDraft = "";
    this._licenseMessage = "";
    this._licenseBusy = null;
    this._notificationTestBusy = null;
    this._notificationTestMessage = "";
    this._notificationSettingsBusy = false;
    this._notificationSettingsMessage = "";
    this._exitSettingsMessage = "";
    this._lastSignature = "";
    this._searchTimer = null;
    this._pendingHassRefresh = false;
    this._pendingSignature = "";
  }

  set hass(hass) {
    this._hass = hass;
    const signature = this._buildSignature();

    // Home Assistant trimite update-uri foarte des pentru toate entitatile din sistem.
    // Panelul se randeaza complet doar daca s-a schimbat semnatura datelor folosite de integrare.
    // In timpul tastarii/editarii nu reconstruim DOM-ul, ca sa nu se piarda focusul pe mobil.
    if (this._isInteractiveControlActive()) {
      this._pendingHassRefresh = signature !== this._lastSignature;
      this._pendingSignature = signature;
      return;
    }

    if (signature === this._lastSignature && this.shadowRoot?.querySelector(".cr-panel")) return;
    this._lastSignature = signature;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    this._render();
  }

  _storageKey(name) {
    return `curieri_romania_panel__${name}`;
  }

  _loadPreference(name) {
    try { return window.localStorage?.getItem(this._storageKey(name)); } catch (_err) { return null; }
  }

  _savePreference(name, value) {
    try { window.localStorage?.setItem(this._storageKey(name), value); } catch (_err) {}
  }

  _isInteractiveControlActive() {
    const active = this.shadowRoot?.activeElement;
    if (!active || typeof active.closest !== "function") return false;
    return Boolean(
      active.closest("form[data-form='license']") ||
      active.closest("form[data-form='notification-settings']") ||
      active.closest("form[data-form='exit-button-settings']") ||
      active.closest(".cr-toolbar")
    );
  }

  _flushPendingHassRefresh() {
    if (!this._pendingHassRefresh) return;
    this._pendingHassRefresh = false;
    const signature = this._pendingSignature || this._buildSignature();
    this._pendingSignature = "";
    if (signature === this._lastSignature && this.shadowRoot?.querySelector(".cr-panel")) return;
    this._lastSignature = signature;
    this._render();
  }

  _buildSignature() {
    if (!this._hass?.states) return "empty";
    const parcels = this._getParcels();
    const license = this._getLicenseInfo();
    return JSON.stringify({
      tab: this._activeTab,
      selected: this._selectedEntityId,
      sort: this._parcelSort,
      search: this._parcelSearch,
      parcels: parcels.map((p) => [p.entityId, p.status, p.lastUpdate, p.pinExpiration, p.hasProblem, p.isFinal]),
      license: [license.status, license.plan, license.expiresAt, license.checkedAt, license.maskedKey, license.message],
      busy: this._licenseBusy,
      message: this._licenseMessage,
      notificationTestBusy: this._notificationTestBusy,
      notificationTestMessage: this._notificationTestMessage,
      notificationSettings: this._getNotificationSettings(),
      notificationDiagnostics: this._getNotificationDiagnostics(),
      notificationHistory: this._getNotificationHistoryDiagnostics(),
      courierDiagnostics: this._getCourierDiagnostics(parcels),
      notificationSettingsBusy: this._notificationSettingsBusy,
      notificationSettingsMessage: this._notificationSettingsMessage,
      exitButtonSettings: this._getExitButtonSettings(),
      exitSettingsMessage: this._exitSettingsMessage,
    });
  }

  _getExitButtonSettings() {
    const enabled = this._loadPreference("exit_button_enabled") === "1";
    const target = this._loadPreference("exit_button_target") || "/lovelace";
    const customUrl = this._loadPreference("exit_button_custom_url") || "";
    const label = this._loadPreference("exit_button_label") || "Iesire";
    return { enabled, target, customUrl, label };
  }

  _saveExitButtonSettings(form) {
    const data = new FormData(form);
    const enabled = data.get("exit_button_enabled") === "on";
    const target = String(data.get("exit_button_target") || "/lovelace").trim() || "/lovelace";
    const customUrl = String(data.get("exit_button_custom_url") || "").trim();
    const label = String(data.get("exit_button_label") || "Iesire").trim() || "Iesire";
    this._savePreference("exit_button_enabled", enabled ? "1" : "0");
    this._savePreference("exit_button_target", target);
    this._savePreference("exit_button_custom_url", customUrl);
    this._savePreference("exit_button_label", label);
    this._exitSettingsMessage = "Setarile butonului de iesire au fost salvate.";
    this._lastSignature = "";
    this._render();
  }

  _resolveExitUrl(settings = this._getExitButtonSettings()) {
    if (settings.target === "custom") return settings.customUrl || "/lovelace";
    return settings.target || "/lovelace";
  }

  _navigateToExitTarget() {
    const url = this._resolveExitUrl();
    if (!url) return;
    if (/^https?:\/\//i.test(url)) {
      window.location.href = url;
      return;
    }
    const target = url.startsWith("/") ? url : `/${url}`;
    window.history.pushState(null, "", target);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _tabs() {
    return [
      ["home", "Acasa", "mdi:view-dashboard-outline"],
      ["parcels", "Colete", "mdi:package-variant-closed"],
      ["license", "Licenta", "mdi:shield-key-outline"],
      ["settings", "Setari", "mdi:cog-outline"],
      ["contact", "Contact", "mdi:email-outline"],
    ];
  }

  _normalize(value) {
    return String(value ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  _entityId(stateObj) {
    if (!stateObj) return null;
    if (stateObj.entity_id) return stateObj.entity_id;
    const states = this._hass?.states || {};
    const found = Object.entries(states).find(([, obj]) => obj === stateObj);
    return found ? found[0] : null;
  }

  _licenseEntity(domain, objectId) {
    const states = this._hass?.states || {};
    const prefixes = ["curieri_romania", "administrare_curieri_romania"];
    const wantedObjectIds = {
      cod_licenta_noua: ["cod_licenta_noua", "cod_licenta_nou", "license_key"],
      aplica_licenta: ["aplica_licenta", "aplicare_licenta", "apply_license"],
      actualizeaza_status_licenta: ["actualizeaza_status_licenta", "actualizare_status_licenta", "refresh_license", "revalidate_license"],
      status_licenta: ["status_licenta"],
      plan_licenta: ["plan_licenta"],
      valabila_pana_la: ["valabila_pana_la", "valabil_pana_la", "expires_at"],
      ultima_verificare_licenta: ["ultima_verificare_licenta", "ultima_verificare", "checked_at"],
      cont_licenta: ["cont_licenta", "utilizator_licenta", "utilizator"],
      cod_licenta_mascat: ["cod_licenta_mascat", "cheie_licenta_mascata", "masked_key"],
      mesaj_licenta: ["mesaj_licenta", "mesaj", "message"],
    }[objectId] || [objectId];

    for (const prefix of prefixes) {
      for (const wanted of wantedObjectIds) {
        const exact = `${domain}.${prefix}_${wanted}`;
        if (states[exact]) return states[exact];
      }
    }

    const entries = Object.entries(states).filter(([entityId]) => {
      if (!entityId.startsWith(`${domain}.`)) return false;
      const objectPart = entityId.split(".")[1] || "";
      if (objectPart.startsWith("car_manager") || objectPart.startsWith("utilitati_romania")) return false;
      return objectPart.includes("curieri_romania") || objectPart.includes("licenta") || objectPart.includes("license");
    });

    const aliases = {
      cod_licenta_noua: ["cod licenta nou", "cod licenta noua", "license key", "licenta noua"],
      aplica_licenta: ["aplica licenta", "aplicare licenta", "apply license"],
      actualizeaza_status_licenta: ["actualizeaza status licenta", "actualizare status licenta", "refresh license", "revalidate license", "verifica licenta"],
      status_licenta: ["status licenta"],
      plan_licenta: ["plan licenta"],
      valabila_pana_la: ["valabila pana la", "valabil pana la", "expires at"],
      ultima_verificare_licenta: ["ultima verificare licenta", "ultima verificare", "checked at"],
      cont_licenta: ["cont licenta", "utilizator licenta", "user licenta"],
      cod_licenta_mascat: ["cod licenta mascat", "cheie licenta mascata", "masked key"],
      mesaj_licenta: ["mesaj licenta", "license message"],
    };
    const allowed = [
      ...wantedObjectIds,
      ...(aliases[objectId] || []),
    ].map((value) => this._normalize(value).replaceAll("_", " "));

    const byFriendlyName = entries.find(([, stateObj]) => {
      const friendlyName = this._normalize(stateObj?.attributes?.friendly_name || "");
      return allowed.some((name) => friendlyName === name || friendlyName.endsWith(` ${name}`));
    });
    if (byFriendlyName) return byFriendlyName[1];

    const byObjectPart = entries.find(([entityId]) => {
      let objectPart = this._normalize(entityId.split(".")[1] || "").replaceAll("_", " ");
      for (const prefix of prefixes) {
        const normalizedPrefix = this._normalize(prefix).replaceAll("_", " ");
        if (objectPart.startsWith(`${normalizedPrefix} `)) {
          objectPart = objectPart.slice(normalizedPrefix.length + 1);
          break;
        }
      }
      return allowed.some((name) => objectPart === name || objectPart.includes(name));
    });
    return byObjectPart ? byObjectPart[1] : null;
  }

  _licenseValue(objectId, domain = "sensor") {
    const stateObj = this._licenseEntity(domain, objectId);
    const value = stateObj?.state;
    if (value === undefined || value === null || value === "" || value === "unknown" || value === "unavailable") return "-";
    return value;
  }

  _getLicenseInfo() {
    const text = this._licenseEntity("text", "cod_licenta_noua");
    const apply = this._licenseEntity("button", "aplica_licenta");
    const refresh = this._licenseEntity("button", "actualizeaza_status_licenta");
    const savedKey = text?.state && !["unknown", "unavailable"].includes(text.state) ? text.state : "";
    if (!this._licenseDraft && savedKey) this._licenseDraft = savedKey;
    return {
      status: this._licenseValue("status_licenta"),
      plan: this._licenseValue("plan_licenta"),
      expiresAt: this._licenseValue("valabila_pana_la"),
      checkedAt: this._licenseValue("ultima_verificare_licenta"),
      account: this._licenseValue("cont_licenta"),
      maskedKey: this._licenseValue("cod_licenta_mascat"),
      message: this._licenseValue("mesaj_licenta"),
      textEntity: text,
      applyButton: apply,
      refreshButton: refresh,
    };
  }

  _notificationEntity(objectId) {
    const states = this._hass?.states || {};
    const prefixes = ["curieri_romania", "administrare_curieri_romania"];
    const wantedObjectIds = {
      notificari_active: ["notificari_active"],
      serviciu_notificare: ["serviciu_notificare"],
      notificari_colet_nou: ["notificari_colet_nou"],
      notificari_schimbare_status: ["notificari_schimbare_status"],
      notificari_in_livrare: ["notificari_in_livrare"],
      notificari_ridicare: ["notificari_ridicare"],
      notificari_livrare_finalizata: ["notificari_livrare_finalizata"],
      notificari_probleme: ["notificari_probleme"],
      notificari_retur: ["notificari_retur"],
      ultima_notificare: ["ultima_notificare"],
      ultimul_titlu_notificare: ["ultimul_titlu_notificare"],
      ultima_tinta_notificare: ["ultima_tinta_notificare"],
      ultimul_rezultat_notificare: ["ultimul_rezultat_notificare"],
      ultima_eroare_notificare: ["ultima_eroare_notificare"],
      istoric_notificari_memorate: ["istoric_notificari_memorate"],
      istoric_colete_reale: ["istoric_colete_reale"],
      istoric_teste_notificari: ["istoric_teste_notificari"],
      ultima_curatare_istoric: ["ultima_curatare_istoric"],
      stari_curatate_ultima_data: ["stari_curatate_ultima_data"],
    }[objectId] || [objectId];

    for (const prefix of prefixes) {
      for (const wanted of wantedObjectIds) {
        const exact = `sensor.${prefix}_${wanted}`;
        if (states[exact]) return states[exact];
      }
    }

    const normalizedWanted = wantedObjectIds.map((value) => this._normalize(value).replaceAll("_", " "));
    const entries = Object.entries(states).filter(([entityId]) => entityId.startsWith("sensor.") && entityId.includes("curieri"));
    const found = entries.find(([entityId, stateObj]) => {
      const objectPart = this._normalize(entityId.split(".")[1] || "").replaceAll("_", " ");
      const friendlyName = this._normalize(stateObj?.attributes?.friendly_name || "");
      return normalizedWanted.some((wanted) => objectPart.includes(wanted) || friendlyName.endsWith(wanted));
    });
    return found ? found[1] : null;
  }

  _notificationValue(objectId) {
    const stateObj = this._notificationEntity(objectId);
    const value = stateObj?.state;
    if (value === undefined || value === null || value === "" || value === "unknown" || value === "unavailable") return "-";
    return value;
  }

  _notificationBool(objectId, defaultValue = true) {
    const value = this._normalize(this._notificationValue(objectId));
    if (value === "-") return defaultValue;
    if (["on", "true", "yes", "da", "activ", "active", "1"].includes(value)) return true;
    if (["off", "false", "no", "nu", "inactiv", "inactive", "0"].includes(value)) return false;
    return defaultValue;
  }

  _getNotificationSettings() {
    return {
      notificationsEnabled: this._notificationBool("notificari_active", true),
      notifyService: this._notificationValue("serviciu_notificare") === "-" ? "" : this._notificationValue("serviciu_notificare"),
      notifyNewParcel: this._notificationBool("notificari_colet_nou", true),
      notifyStatusChange: this._notificationBool("notificari_schimbare_status", true),
      notifyOutForDelivery: this._notificationBool("notificari_in_livrare", true),
      notifyPickup: this._notificationBool("notificari_ridicare", true),
      notifyDelivered: this._notificationBool("notificari_livrare_finalizata", true),
      notifyProblems: this._notificationBool("notificari_probleme", true),
      notifyReturned: this._notificationBool("notificari_retur", true),
    };
  }

  _getNotificationDiagnostics() {
    return {
      lastAt: this._notificationValue("ultima_notificare"),
      lastTitle: this._notificationValue("ultimul_titlu_notificare"),
      lastTarget: this._notificationValue("ultima_tinta_notificare"),
      lastResult: this._notificationValue("ultimul_rezultat_notificare"),
      lastError: this._notificationValue("ultima_eroare_notificare"),
    };
  }

  _getNotificationHistoryDiagnostics() {
    return {
      totalStates: this._notificationValue("istoric_notificari_memorate"),
      realStates: this._notificationValue("istoric_colete_reale"),
      testStates: this._notificationValue("istoric_teste_notificari"),
      lastCleanupAt: this._notificationValue("ultima_curatare_istoric"),
      lastCleanupRemoved: this._notificationValue("stari_curatate_ultima_data"),
    };
  }

  _aggregateDiagnosticAttributes() {
    const states = this._hass?.states || {};
    const candidates = Object.values(states).filter((stateObj) => {
      const attrs = stateObj?.attributes || {};
      return attrs.by_courier || attrs.debug || attrs.errors || attrs.parcels_total !== undefined;
    });
    return candidates.find((stateObj) => {
      const entityId = this._entityId(stateObj) || "";
      const name = this._normalize(stateObj?.attributes?.friendly_name || "");
      return entityId.includes("curieri") || name.includes("curieri");
    })?.attributes || {};
  }

  _getCourierDiagnostics(parcels = this._getParcels()) {
    const attrs = this._aggregateDiagnosticAttributes();
    const errors = attrs.errors && typeof attrs.errors === "object" ? attrs.errors : {};
    const debug = attrs.debug && typeof attrs.debug === "object" ? attrs.debug : {};
    const providers = debug.providers && typeof debug.providers === "object" ? debug.providers : {};
    const byKey = new Map();
    const normalizeCourier = (value) => this._normalize(value || "necunoscut").replaceAll(" ", "_");

    const ensure = (courier) => {
      const label = courier || "Necunoscut";
      const key = normalizeCourier(label);
      if (!byKey.has(key)) {
        byKey.set(key, { courier: label, total: 0, active: 0, delivered: 0, problems: 0, lastUpdate: "", error: "" });
      }
      return byKey.get(key);
    };

    parcels.forEach((parcel) => {
      const item = ensure(parcel.courier);
      item.total += 1;
      if (!parcel.isFinal) item.active += 1;
      if (parcel.status === "livrat") item.delivered += 1;
      if (parcel.hasProblem || ["problema", "livrare esuata", "returnat"].includes(parcel.status)) item.problems += 1;
      const ts = this._parcelTimestamp(parcel);
      if (ts > this._parseTimestamp(item.lastUpdate)) item.lastUpdate = new Date(ts).toISOString();
    });

    Object.entries(providers).forEach(([providerKey, info]) => {
      const providerLabel = { sameday: "Sameday", fan_courier: "FAN Courier", cargus: "Cargus", gls: "GLS" }[providerKey] || providerKey;
      const item = ensure(providerLabel);
      const count = Number(info?.parcel_count);
      if (Number.isFinite(count) && item.total === 0) item.total = count;
      if (info?.error) item.error = String(info.error);
    });

    Object.entries(errors).forEach(([providerKey, error]) => {
      const providerLabel = { sameday: "Sameday", fan_courier: "FAN Courier", cargus: "Cargus", gls: "GLS", license: "Licenta" }[providerKey] || providerKey;
      ensure(providerLabel).error = String(error || "Eroare");
    });

    return Array.from(byKey.values()).sort((a, b) => a.courier.localeCompare(b.courier, "ro"));
  }

  _notificationDiagnosticSensorIds() {
    return [
      "ultima_notificare",
      "ultimul_titlu_notificare",
      "ultima_tinta_notificare",
      "ultimul_rezultat_notificare",
      "ultima_eroare_notificare",
      "istoric_notificari_memorate",
      "istoric_colete_reale",
      "istoric_teste_notificari",
      "ultima_curatare_istoric",
      "stari_curatate_ultima_data",
    ].map((key) => this._entityId(this._notificationEntity(key))).filter(Boolean);
  }



  _getDetectedNotifyServices(currentValue = "") {
    const options = [{ value: "", label: "Notificare persistenta Home Assistant" }];
    const seen = new Set([""]);
    const addOption = (value, label = null) => {
      const clean = String(value || "").trim();
      if (!clean || seen.has(clean)) return;
      seen.add(clean);
      options.push({ value: clean, label: label || clean });
    };

    const notifyServices = this._hass?.services?.notify || {};
    Object.keys(notifyServices)
      .filter((service) => service && !service.startsWith("_") && service !== "reload")
      .sort((a, b) => a.localeCompare(b, "ro"))
      .forEach((service) => addOption(`notify.${service}`));

    Object.keys(this._hass?.states || {})
      .filter((entityId) => entityId.startsWith("notify."))
      .sort((a, b) => a.localeCompare(b, "ro"))
      .forEach((entityId) => addOption(entityId, `${entityId} (entitate)`));

    const current = String(currentValue || "").trim();
    if (current && !seen.has(current)) addOption(current, `${current} (salvat)`);
    return options;
  }

  _hasValidLicenseStatus(status) {
    const value = this._normalize(status || "");
    return /active|activ|trial/.test(value) && !/inactive|inactiv|invalid|expir|revoc|limita|eroare/.test(value);
  }

  _licenseClass(status) {
    const value = this._normalize(status || "");
    if (this._hasValidLicenseStatus(status)) return "is-good";
    if (/expired|expir|invalid|revoked|revoc|activation|limita|inactive|inactiv/.test(value)) return "is-bad";
    if (/unknown|necunoscut|neverificat|eroare/.test(value)) return "is-warn";
    return "is-neutral";
  }

  _getParcels() {
    if (!this._hass?.states) return [];
    return Object.entries(this._hass.states)
      .map(([entityId, state]) => ({ entityId, state }))
      .filter(({ state }) => state?.attributes?.courier && state?.attributes?.awb)
      .map(({ entityId, state }) => ({
        entityId,
        status: state.state,
        awb: state.attributes.awb || "",
        awbShort: state.attributes.awb_short || state.attributes.awb || "",
        courier: state.attributes.courier || "",
        direction: state.attributes.direction || "",
        originalStatus: state.attributes.original_status || "Necunoscut",
        sender: state.attributes.sender || "Necunoscut",
        recipient: state.attributes.recipient || "Necunoscut",
        location: state.attributes.current_location || state.attributes.locker_name || "Necunoscut",
        lockerName: state.attributes.locker_name || "",
        cod: state.attributes.cash_on_delivery,
        lastUpdate: state.attributes.last_update,
        pinExpiration: state.attributes.pin_expiration_at,
        deliveredAt: state.attributes.delivered_at,
        isLocker: state.attributes.is_locker === true,
        isPickupPoint: state.attributes.is_pickup_point === true,
        hasProblem: state.attributes.has_problem === true,
        isFinal: state.attributes.is_final === true,
        friendlyName: state.attributes.friendly_name || state.attributes.display_name || entityId,
        events: Array.isArray(state.attributes.events) ? state.attributes.events : [],
        attributes: state.attributes || {},
      }));
  }

  _parseTimestamp(value) {
    if (!value) return 0;
    const parsed = new Date(value).getTime();
    return Number.isFinite(parsed) ? parsed : 0;
  }

  _parcelTimestamp(parcel) {
    const eventTimes = Array.isArray(parcel.events)
      ? parcel.events.map((event) => this._parseTimestamp(this._eventTime(event))).filter((value) => value > 0)
      : [];
    return Math.max(
      this._parseTimestamp(parcel.lastUpdate),
      this._parseTimestamp(parcel.deliveredAt),
      this._parseTimestamp(parcel.pinExpiration),
      eventTimes.length ? Math.max(...eventTimes) : 0,
    );
  }

  _statusWeight(parcel) {
    if (parcel.hasProblem || ["returnat", "problema", "livrare esuata"].includes(parcel.status)) return 0;
    if (!parcel.isFinal && parcel.status === "disponibil la locker") return 1;
    if (!parcel.isFinal && parcel.status === "disponibil la punct de ridicare") return 2;
    if (!parcel.isFinal) return 3;
    if (parcel.status === "livrat") return 4;
    return 5;
  }

  _searchableText(parcel) {
    return this._normalize([
      parcel.awb, parcel.awbShort, parcel.courier, parcel.status, parcel.originalStatus,
      parcel.sender, parcel.recipient, parcel.location, parcel.lockerName, parcel.direction,
    ].filter(Boolean).join(" "));
  }

  _sortParcels(parcels, sortMode = this._parcelSort) {
    const items = [...parcels];
    const byNewest = (a, b) => this._parcelTimestamp(b) - this._parcelTimestamp(a) || a.awb.localeCompare(b.awb);
    const byOldest = (a, b) => this._parcelTimestamp(a) - this._parcelTimestamp(b) || a.awb.localeCompare(b.awb);
    const sorters = {
      newest: byNewest,
      oldest: byOldest,
      courier: (a, b) => a.courier.localeCompare(b.courier) || byNewest(a, b),
      status: (a, b) => this._statusWeight(a) - this._statusWeight(b) || byNewest(a, b),
      awb: (a, b) => a.awb.localeCompare(b.awb) || byNewest(a, b),
      sender: (a, b) => String(a.sender || "").localeCompare(String(b.sender || "")) || byNewest(a, b),
      active: (a, b) => Number(a.isFinal) - Number(b.isFinal) || byNewest(a, b),
      ready: (a, b) => Number(!["disponibil la locker", "disponibil la punct de ridicare"].includes(a.status)) - Number(!["disponibil la locker", "disponibil la punct de ridicare"].includes(b.status)) || byNewest(a, b),
      problems: (a, b) => Number(!a.hasProblem) - Number(!b.hasProblem) || byNewest(a, b),
      delivered: (a, b) => Number(a.status !== "livrat") - Number(b.status !== "livrat") || byNewest(a, b),
    };
    items.sort(sorters[sortMode] || byNewest);
    return items;
  }

  _filteredParcels(parcels) {
    const query = this._normalize(this._parcelSearch);
    const sorted = this._sortParcels(parcels, this._parcelSort);
    if (!query) return sorted;
    return sorted.filter((parcel) => this._searchableText(parcel).includes(query));
  }

  _setParcelSort(value) {
    this._parcelSort = value || "newest";
    this._savePreference("parcel_sort", this._parcelSort);
    this._render();
  }

  _setParcelSearch(value) {
    this._parcelSearch = value || "";
    this._lastSignature = "";
    this._render();
  }

  _statusClass(parcel) {
    if (parcel.hasProblem || ["returnat", "problema", "livrare esuata"].includes(parcel.status)) return "is-bad";
    if (parcel.status === "livrat") return "is-neutral";
    if (["disponibil la locker", "disponibil la punct de ridicare"].includes(parcel.status)) return "is-good";
    return "is-info";
  }

  _formatDate(value) {
    if (!value || value === "-") return "-";
    try {
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value).replace(/[\sT]+\d{1,2}:\d{2}(?::\d{2})?.*$/, "");
      return new Intl.DateTimeFormat("ro-RO", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
    } catch (_err) {
      return String(value);
    }
  }

  _formatMoney(value) {
    if (value === null || value === undefined || value === "") return "-";
    const number = Number(value);
    if (!Number.isFinite(number)) return this._escape(String(value));
    if (number === 0) return "0 lei";
    return new Intl.NumberFormat("ro-RO", { style: "currency", currency: "RON" }).format(number);
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _openEntity(entityId) {
    if (!entityId) return;
    const ev = new Event("hass-more-info", { bubbles: true, composed: true });
    ev.detail = { entityId };
    this.dispatchEvent(ev);
  }

  _openDetails(entityId) {
    this._selectedEntityId = entityId;
    this._render();
  }

  _closeDetails() {
    this._selectedEntityId = null;
    this._render();
  }

  _openHelper(helper) {
    const urls = {
      sameday: "/curieri_romania_tools/sameday_refresh_token_helper.html",
      cargus: "/curieri_romania_tools/cargus_refresh_token_helper.html",
    };
    if (urls[helper]) window.open(urls[helper], "_blank", "noopener,noreferrer");
  }

  async _applyLicense(value) {
    if (!this._hass || this._licenseBusy) return;
    const license = this._getLicenseInfo();
    const textEntityId = this._entityId(license.textEntity);
    const applyEntityId = this._entityId(license.applyButton);
    if (!textEntityId || !applyEntityId) {
      this._licenseMessage = "Entitatile de licentiere nu sunt disponibile. Fa restart Home Assistant dupa actualizare.";
      this._render();
      return;
    }
    try {
      this._licenseBusy = "apply";
      this._licenseMessage = "Se aplica licenta...";
      this._render();
      const licenseKey = String(value || "TRIAL").trim() || "TRIAL";
      await this._hass.callService("text", "set_value", { value: licenseKey }, { entity_id: textEntityId });
      await this._hass.callService("button", "press", {}, { entity_id: applyEntityId });
      this._licenseDraft = licenseKey;
      this._licenseMessage = "Licenta a fost trimisa pentru validare. Statusul se actualizeaza imediat ce Home Assistant reimprospateaza senzorii.";
      await this._refreshLicenseEntities(false);
    } catch (error) {
      this._licenseMessage = error?.message || "Nu am putut aplica licenta.";
    } finally {
      this._licenseBusy = null;
      this._lastSignature = "";
      this._render();
    }
  }

  async _refreshLicenseEntities(renderAfter = true) {
    if (!this._hass || this._licenseBusy === "refresh") return;
    const license = this._getLicenseInfo();
    const refreshEntityId = this._entityId(license.refreshButton);
    const sensorIds = [
      this._entityId(this._licenseEntity("sensor", "status_licenta")),
      this._entityId(this._licenseEntity("sensor", "plan_licenta")),
      this._entityId(this._licenseEntity("sensor", "valabila_pana_la")),
      this._entityId(this._licenseEntity("sensor", "ultima_verificare_licenta")),
      this._entityId(this._licenseEntity("sensor", "cont_licenta")),
      this._entityId(this._licenseEntity("sensor", "cod_licenta_mascat")),
      this._entityId(this._licenseEntity("sensor", "mesaj_licenta")),
    ].filter(Boolean);

    try {
      this._licenseBusy = "refresh";
      this._licenseMessage = "Se verifica licenta online...";
      this._render();
      if (this._hass.services?.curieri_romania?.refresh_license_status) {
        await this._hass.callService("curieri_romania", "refresh_license_status", {});
      } else if (refreshEntityId) {
        await this._hass.callService("button", "press", {}, { entity_id: refreshEntityId });
      } else {
        throw new Error("Nu am gasit serviciul sau butonul pentru actualizarea licentei.");
      }
      this._licenseMessage = "Statusul licentei a fost verificat online.";
    } catch (error) {
      this._licenseMessage = error?.message || "Nu am putut verifica licenta.";
    }

    if (sensorIds.length) {
      try { await this._hass.callService("homeassistant", "update_entity", { entity_id: sensorIds }); } catch (_err) {}
    }

    this._licenseBusy = null;
    this._notificationTestBusy = null;
    this._notificationTestMessage = "";
    this._notificationSettingsBusy = false;
    this._notificationSettingsMessage = "";
    this._exitSettingsMessage = "";
    this._lastSignature = "";
    if (renderAfter) this._render();
  }

  async _saveNotificationSettings(form) {
    if (!this._hass || this._notificationSettingsBusy) return;
    const data = new FormData(form);
    const payload = {
      notifications_enabled: data.get("notifications_enabled") === "on",
      notify_service: String(data.get("notify_service") || "").trim(),
      notify_new_parcel: data.get("notify_new_parcel") === "on",
      notify_status_change: data.get("notify_status_change") === "on",
      notify_out_for_delivery: data.get("notify_out_for_delivery") === "on",
      notify_pickup: data.get("notify_pickup") === "on",
      notify_delivered: data.get("notify_delivered") === "on",
      notify_problems: data.get("notify_problems") === "on",
      notify_returned: data.get("notify_returned") === "on",
    };

    try {
      this._notificationSettingsBusy = true;
      this._notificationSettingsMessage = "Se salveaza setarile...";
      this._lastSignature = "";
      this._render();
      await this._hass.callService("curieri_romania", "update_notification_settings", payload);
      const sensorIds = [
        this._entityId(this._notificationEntity("notificari_active")),
        this._entityId(this._notificationEntity("serviciu_notificare")),
        this._entityId(this._notificationEntity("notificari_colet_nou")),
        this._entityId(this._notificationEntity("notificari_schimbare_status")),
        this._entityId(this._notificationEntity("notificari_in_livrare")),
        this._entityId(this._notificationEntity("notificari_ridicare")),
        this._entityId(this._notificationEntity("notificari_livrare_finalizata")),
        this._entityId(this._notificationEntity("notificari_probleme")),
        this._entityId(this._notificationEntity("notificari_retur")),
        ...this._notificationDiagnosticSensorIds(),
      ].filter(Boolean);
      if (sensorIds.length) {
        try { await this._hass.callService("homeassistant", "update_entity", { entity_id: sensorIds }); } catch (_err) {}
      }
      this._notificationSettingsMessage = "Setarile de notificare au fost salvate.";
    } catch (error) {
      this._notificationSettingsMessage = error?.message || "Nu am putut salva setarile de notificare.";
    } finally {
      this._notificationSettingsBusy = false;
      this._lastSignature = "";
      this._render();
    }
  }


  async _callNotificationTest(serviceName, busyKey, successMessage) {
    if (!this._hass || this._notificationTestBusy) return;
    try {
      this._notificationTestBusy = busyKey;
      this._notificationTestMessage = "Se trimite testul...";
      this._lastSignature = "";
      this._render();
      await this._hass.callService("curieri_romania", serviceName, {});
      const sensorIds = this._notificationDiagnosticSensorIds();
      if (sensorIds.length) {
        try { await this._hass.callService("homeassistant", "update_entity", { entity_id: sensorIds }); } catch (_err) {}
      }
      this._notificationTestMessage = successMessage;
    } catch (error) {
      this._notificationTestMessage = error?.message || "Nu am putut trimite testul de notificare.";
    } finally {
      this._notificationTestBusy = null;
      this._lastSignature = "";
      this._render();
    }
  }

  _setTab(tab) {
    this._activeTab = tab;
    this._savePreference("active_tab", tab);
    this._selectedEntityId = null;
    this._render();
  }

  _render() {
    if (!this.shadowRoot) return;
    const parcels = this._getParcels();
    const license = this._getLicenseInfo();
    const active = parcels.filter((p) => !p.isFinal).length;
    const delivered = parcels.filter((p) => p.status === "livrat").length;
    const problems = parcels.filter((p) => p.hasProblem || ["returnat", "problema", "livrare esuata"].includes(p.status)).length;
    const ready = parcels.filter((p) => p.status === "disponibil la locker" || p.status === "disponibil la punct de ridicare").length;
    const selectedParcel = parcels.find((parcel) => parcel.entityId === this._selectedEntityId) || null;
    const exitSettings = this._getExitButtonSettings();

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <div class="cr-panel">
        <header class="cr-hero">
          <div class="cr-hero-main">
            <div class="cr-logo"><img src="/curieri_romania_static/assets/curieri-romania-logo.png?v=1.0.1" alt="Curieri Romania"></div>
            <div>
              <h1>Curieri Romania</h1>
              <p>Monitorizare colete si statusuri curieri direct in Home Assistant.</p>
            </div>
            ${exitSettings.enabled ? `<button type="button" class="cr-exit-button" data-action="panel-exit" title="${this._escape(this._resolveExitUrl(exitSettings))}"><ha-icon icon="mdi:exit-to-app"></ha-icon><span>${this._escape(exitSettings.label || "Iesire")}</span></button>` : ""}
            <a class="cr-hero-badge" href="https://haforgelabs.ro" target="_blank" rel="noopener noreferrer" title="Deschide HAForge Labs"><img src="/curieri_romania_static/assets/haforge-logo.png?v=1.0.1" alt="HAForge Labs"><span>HAForge Labs</span><small>v1.0.1</small></a>
          </div>
          <aside class="cr-hero-side">
            ${this._sideMetric("Licenta", this._licenseShortStatus(license.status), this._licenseClass(license.status))}
            ${this._sideMetric("Colete active", active, "is-info")}
            ${this._sideMetric("De ridicat", ready, ready ? "is-good" : "is-neutral")}
            ${this._sideMetric("Probleme", problems, problems ? "is-bad" : "is-neutral")}
          </aside>
        </header>
        <div class="cr-tabs-shell">
          <nav class="cr-tabs" aria-label="Navigare Curieri Romania">
            ${this._tabs().map(([id, label, icon]) => `
              <button type="button" class="cr-tab ${this._activeTab === id ? "active" : ""}" data-tab="${id}" title="${this._escape(label)}">
                <ha-icon icon="${icon}"></ha-icon><span>${this._escape(label)}</span>
              </button>
            `).join("")}
          </nav>
        </div>
        ${this._renderContent(parcels, license, { active, delivered, problems, ready })}
        ${selectedParcel ? this._detailDrawer(selectedParcel) : ""}
      </div>
    `;
    this._bindEvents();
  }

  _licenseShortStatus(status) {
    if (this._hasValidLicenseStatus(status)) return "OK";
    const value = String(status || "-");
    return value === "-" ? "-" : value;
  }

  _sideMetric(label, value, cls) {
    return `<div class="cr-side-card ${cls}"><span>${this._escape(label)}</span><strong>${this._escape(value)}</strong></div>`;
  }

  _renderContent(parcels, license, summary) {
    if (this._activeTab === "parcels") return this._renderParcelsPage(parcels);
    if (this._activeTab === "license") return this._renderLicensePage(license);
    if (this._activeTab === "settings") return this._renderSettingsPage();
    if (this._activeTab === "contact") return this._renderContactPage();
    return this._renderHomePage(parcels, summary);
  }

  _renderHomePage(parcels, summary) {
    const latest = this._sortParcels(parcels, "newest").slice(0, 6);
    return `
      <main class="cr-page">
        <div class="cr-page-title">
          <span>Acasa</span>
          <h2>Privire de ansamblu</h2>
          <p>Statusurile importante si ultimele colete detectate.</p>
        </div>
        <section class="cr-kpis">
          ${this._kpi("Colete active", summary.active, "in lucru", "mdi:package-variant-closed", "is-info")}
          ${this._kpi("Disponibile", summary.ready, "locker / punct ridicare", "mdi:package-check", summary.ready ? "is-good" : "is-neutral")}
          ${this._kpi("Livrate", summary.delivered, "finalizate", "mdi:check-circle-outline", "is-neutral")}
          ${this._kpi("Probleme", summary.problems, "returnate / esuate", "mdi:alert-circle-outline", summary.problems ? "is-bad" : "is-neutral")}
        </section>
        <section class="cr-section">
          <div class="cr-section-head"><div><span>Recente</span><h3>Ultimele colete</h3></div><button type="button" data-tab="parcels">Vezi toate</button></div>
          ${latest.length ? `<div class="cr-card-grid">${latest.map((parcel) => this._parcelCard(parcel)).join("")}</div>` : `<div class="cr-empty">Nu exista colete inregistrate momentan.</div>`}
        </section>
      </main>
    `;
  }

  _renderParcelsPage(parcels) {
    return `
      <main class="cr-page">
        <div class="cr-page-title">
          <span>Colete</span>
          <h2>Administrare colete</h2>
          <p>Lista coletelor detectate prin curierii configurati.</p>
        </div>
        ${this._renderParcelToolbar(parcels)}
        ${this._renderParcelList(parcels)}
      </main>
    `;
  }

  _sortLabel(value) {
    const labels = {
      newest: "cele mai noi",
      oldest: "cele mai vechi",
      courier: "curier",
      status: "status important",
      awb: "AWB",
      sender: "expeditor",
      active: "active primele",
      ready: "de ridicat primele",
      problems: "probleme primele",
      delivered: "livrate primele",
    };
    return labels[value] || labels.newest;
  }

  _renderParcelToolbar(parcels) {
    const filtered = this._filteredParcels(parcels);
    const options = [
      ["newest", "Cele mai noi"],
      ["oldest", "Cele mai vechi"],
      ["courier", "Curier"],
      ["status", "Status / prioritate"],
      ["ready", "Disponibile la ridicare"],
      ["active", "Active primele"],
      ["problems", "Probleme primele"],
      ["delivered", "Livrate primele"],
      ["awb", "AWB"],
      ["sender", "Expeditor"],
    ];
    return `
      <section class="cr-toolbar">
        <div class="cr-toolbar-info" data-role="parcel-count">
          <strong>${this._escape(filtered.length)}</strong>
          <span>din ${this._escape(parcels.length)} colete · sortare dupa ${this._escape(this._sortLabel(this._parcelSort))}</span>
        </div>
        <label class="cr-search">
          <ha-icon icon="mdi:magnify"></ha-icon>
          <input type="search" value="${this._escape(this._parcelSearch)}" placeholder="Cauta AWB, curier, status, expeditor..." />
        </label>
        <label class="cr-sort">
          <span>Sorteaza</span>
          <select>
            ${options.map(([value, label]) => `<option value="${value}" ${this._parcelSort === value ? "selected" : ""}>${this._escape(label)}</option>`).join("")}
          </select>
        </label>
      </section>
    `;
  }

  _renderParcelList(parcels) {
    return `<div class="cr-parcel-list-host" data-role="parcel-list">${this._renderParcelListInner(parcels)}</div>`;
  }

  _renderParcelListInner(parcels) {
    if (!parcels.length) return `<div class="cr-empty">Nu exista colete inregistrate momentan.</div>`;
    const filtered = this._filteredParcels(parcels);
    if (!filtered.length) return `<div class="cr-empty">Nu exista colete care corespund cautarii curente.</div>`;
    return this._renderGroupedParcels(filtered);
  }

  _updateParcelListOnly() {
    if (!this.shadowRoot || this._activeTab !== "parcels") return;
    const parcels = this._getParcels();
    const filtered = this._filteredParcels(parcels);
    const count = this.shadowRoot.querySelector("[data-role='parcel-count']");
    if (count) {
      count.innerHTML = `<strong>${this._escape(filtered.length)}</strong><span>din ${this._escape(parcels.length)} colete · sortare dupa ${this._escape(this._sortLabel(this._parcelSort))}</span>`;
    }
    const host = this.shadowRoot.querySelector("[data-role='parcel-list']");
    if (host) {
      host.innerHTML = this._renderParcelListInner(parcels);
      this._bindParcelCardEvents(host);
    }
  }

  _bindParcelCardEvents(root = this.shadowRoot) {
    for (const card of root.querySelectorAll(".cr-parcel-card[data-entity]")) {
      card.addEventListener("click", () => this._openDetails(card.getAttribute("data-entity")));
    }
  }

  _renderGroupedParcels(parcels) {
    const groups = this._groupParcels(parcels, this._parcelSort);
    return `<div class="cr-grouped-list">${groups.map((group) => `
      <section class="cr-parcel-group">
        <div class="cr-group-header">
          <div><span>${this._escape(group.meta || "Grup")}</span><h3>${this._escape(group.title)}</h3></div>
          <strong>${this._escape(group.items.length)} ${group.items.length === 1 ? "colet" : "colete"}</strong>
        </div>
        <div class="cr-card-grid">${group.items.map((parcel) => this._parcelCard(parcel)).join("")}</div>
      </section>
    `).join("")}</div>`;
  }

  _groupParcels(parcels, sortMode) {
    const groupers = {
      newest: (parcel) => this._monthGroup(parcel),
      oldest: (parcel) => this._monthGroup(parcel),
      courier: (parcel) => ({ key: this._normalize(parcel.courier || "Necunoscut"), title: parcel.courier || "Necunoscut", meta: "Curier" }),
      status: (parcel) => ({ key: String(this._statusWeight(parcel)).padStart(2, "0") + this._normalize(parcel.status || "Necunoscut"), title: parcel.status || "Necunoscut", meta: "Status" }),
      ready: (parcel) => {
        const ready = ["disponibil la locker", "disponibil la punct de ridicare"].includes(parcel.status);
        return { key: ready ? "0_ready" : "1_other", title: ready ? "Disponibile la ridicare" : "Alte colete", meta: "Ridicare" };
      },
      active: (parcel) => ({ key: parcel.isFinal ? "1_final" : "0_active", title: parcel.isFinal ? "Finalizate" : "Active", meta: "Activitate" }),
      problems: (parcel) => ({ key: parcel.hasProblem ? "0_problem" : "1_ok", title: parcel.hasProblem ? "Cu probleme" : "Fara probleme", meta: "Probleme" }),
      delivered: (parcel) => ({ key: parcel.status === "livrat" ? "0_livrat" : "1_other", title: parcel.status === "livrat" ? "Livrate" : "Nelivrate / in lucru", meta: "Livrare" }),
      awb: (parcel) => {
        const first = String(parcel.awb || parcel.awbShort || "?").trim().slice(0, 1).toUpperCase() || "?";
        return { key: first, title: `AWB ${first}`, meta: "AWB" };
      },
      sender: (parcel) => ({ key: this._normalize(parcel.sender || "Necunoscut"), title: parcel.sender || "Necunoscut", meta: "Expeditor" }),
    };
    const grouper = groupers[sortMode] || groupers.newest;
    const map = new Map();
    for (const parcel of parcels) {
      const group = grouper(parcel);
      const key = group.key || "necunoscut";
      if (!map.has(key)) map.set(key, { ...group, items: [] });
      map.get(key).items.push(parcel);
    }
    return Array.from(map.values());
  }

  _monthGroup(parcel) {
    const timestamp = this._parcelTimestamp(parcel);
    if (!timestamp) return { key: "9999_necunoscuta", title: "Data necunoscuta", meta: "Perioada" };
    const date = new Date(timestamp);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    const title = new Intl.DateTimeFormat("ro-RO", { month: "long", year: "numeric" }).format(date);
    return { key, title: title.charAt(0).toUpperCase() + title.slice(1), meta: "Luna actualizare" };
  }

  _renderLicensePage(license) {
    const statusClass = this._licenseClass(license.status);
    const textEntityId = this._entityId(license.textEntity);
    const applyEntityId = this._entityId(license.applyButton);
    const refreshEntityId = this._entityId(license.refreshButton);
    const draft = this._licenseDraft || (license.textEntity?.state && !["unknown", "unavailable"].includes(license.textEntity.state) ? license.textEntity.state : "");
    const hasValidLicense = this._hasValidLicenseStatus(license.status);
    const normalizedPlan = this._normalize(license.plan || "");
    const isTrialLicense = hasValidLicense && /trial|test|demo/.test(normalizedPlan);
    const supportTitle = hasValidLicense && !isTrialLicense
      ? "Continua sa sustii dezvoltarea proiectului"
      : "Sustine proiectul si obtine licenta";
    const supportIntro = hasValidLicense && !isTrialLicense
      ? "Ai deja o licenta activa. Daca integrarea iti este utila, poti sustine in continuare dezvoltarea, mentenanta si adaptarea proiectului atunci cand Home Assistant sau portalurile curierilor se schimba."
      : "Licenta se obtine printr-o donatie pe Buy Me a Coffee. Donatia ajuta la dezvoltare, testare, mentenanta si suport pentru integrare.";
    const supportNote = hasValidLicense && !isTrialLicense
      ? "Donatiile suplimentare nu sunt obligatorii, dar ajuta la mentinerea proiectului activ."
      : "Dupa donatie, te rog sa mentionezi clar integrarea \"Curieri Romania\" si adresa de email pe care doresti sa primesti cheia de licenta.";
    const supportButton = hasValidLicense && !isTrialLicense
      ? "Sustine proiectul prin Buy Me a Coffee"
      : "Obtine licenta prin Buy Me a Coffee";
    return `
      <main class="cr-page cr-license-page">
        <div class="cr-page-title">
          <span>Licenta</span>
          <h2>Licenta Curieri Romania</h2>
          <p>Licenta activa deblocheaza toti curierii configurati. Fara licenta ramane disponibil primul curier.</p>
        </div>
        <section class="cr-license-hero ${statusClass}">
          <div>
            <span>Status licenta</span>
            <strong>${this._escape(this._licenseShortStatus(license.status))}</strong>
            <p>${this._escape(license.message && license.message !== "-" ? license.message : "Statusul este citit din entitatile de administrare ale integrarii.")}</p>
          </div>
          <ha-icon icon="mdi:shield-key-outline"></ha-icon>
        </section>
        <section class="cr-license-grid">
          ${this._licenseTile("Plan", license.plan, "mdi:badge-account-horizontal-outline")}
          ${this._licenseTile("Cod licenta", license.maskedKey, "mdi:key-outline")}
          ${this._licenseTile("Valabila pana la", license.expiresAt, "mdi:calendar-check-outline")}
          ${this._licenseTile("Ultima verificare", this._formatDate(license.checkedAt), "mdi:clock-check-outline")}
          ${this._licenseTile("Cont", license.account, "mdi:account-outline")}
          ${this._licenseTile("Mesaj", license.message, "mdi:message-text-outline")}
        </section>
        <section class="cr-contact-note">
          <h3>Confidentialitate licentiere</h3>
          <p>La verificarea licentei se trimit doar cheia de licenta, produsul, versiunea si un hash anonim al instalarii. Nu se trimit date despre colete, AWB-uri, adrese, telefoane, parole, tokenuri sau istoric de livrare.</p>
        </section>
        <section class="cr-form-card">
          <div class="cr-section-head"><div><span>Activare</span><h3>Introdu codul de licenta</h3></div></div>
          <form data-form="license">
            <label>
              <span>Cod licenta</span>
              <input name="license_key" autocomplete="off" value="${this._escape(draft)}" placeholder="CRRO-XXXX-XXXX-XXXX" />
            </label>
            <button type="submit" ${this._licenseBusy === "apply" ? "disabled" : ""}>${this._licenseBusy === "apply" ? "Se aplica..." : "Aplica licenta"}</button>
          </form>
          <p class="cr-help">Entitate text: ${this._escape(textEntityId || "negasita")} · buton aplicare: ${this._escape(applyEntityId || "negasit")}</p>
          ${(!textEntityId || !applyEntityId) ? `<div class="cr-message is-warn">Entitatile de licentiere nu sunt disponibile inca. De obicei se rezolva dupa restart Home Assistant si golire cache browser.</div>` : ""}
          ${this._licenseMessage ? `<div class="cr-message">${this._escape(this._licenseMessage)}</div>` : ""}
        </section>
        <section class="cr-action-row">
          <div><h3>Verificare licenta</h3><p>Actualizeaza manual statusul licentei salvate local.</p><small>Buton detectat: ${this._escape(refreshEntityId || "negasit")}</small></div>
          <button type="button" data-action="license-refresh" ${this._licenseBusy === "refresh" ? "disabled" : ""}><ha-icon icon="mdi:shield-sync-outline"></ha-icon>${this._licenseBusy === "refresh" ? "Se verifica..." : "Actualizeaza status"}</button>
        </section>
        <section class="cr-license-support">
          <div class="cr-license-support-icon"><ha-icon icon="mdi:heart-outline"></ha-icon></div>
          <div>
            <h3>${this._escape(supportTitle)}</h3>
            <p>${this._escape(supportIntro)}</p>
            <p>${this._escape(supportNote)}</p>
            <a href="https://www.buymeacoffee.com/haforgelabs" target="_blank" rel="noopener noreferrer">
              <span>☕</span>
              ${this._escape(supportButton)}
            </a>
            <small>Multumim pentru sustinere si pentru folosirea integrarii.</small>
          </div>
        </section>
      </main>
    `;
  }

  _renderContactPage() {
    return `
      <main class="cr-page cr-contact-page">
        <div class="cr-page-title">
          <span>Contact</span>
          <h2>Contact si suport HAForge Labs</h2>
          <p>Informatii utile pentru suport, licente, raportare probleme si dezvoltarea integrarii Curieri Romania.</p>
        </div>
        <section class="cr-contact-hero">
          <img src="/curieri_romania_static/assets/haforge-logo.png?v=1.0.1" alt="HAForge Labs">
          <div>
            <span>HAForge Labs</span>
            <h3>Curieri Romania pentru Home Assistant</h3>
            <p>Pentru licente, suport sau raportarea unei probleme, trimite cat mai multe detalii relevante, dar fara parole, tokenuri, cookie-uri sau capturi cu date personale vizibile.</p>
          </div>
        </section>
        <section class="cr-contact-grid">
          ${this._contactCard("Website", "haforgelabs.ro", "Pagina oficiala HAForge Labs.", "mdi:web", "https://haforgelabs.ro")}
          ${this._contactCard("Buy Me a Coffee", "Sustinere si licente", "La donatie, mentioneaza clar integrarea Curieri Romania si emailul pentru licenta.", "mdi:coffee-outline", "https://www.buymeacoffee.com/haforgelabs")}
          ${this._contactCard("Email", "contact@haforgelabs.ro", "Pentru suport si solicitari legate de licenta.", "mdi:email-outline", "mailto:contact@haforgelabs.ro?subject=Curieri%20Romania")}
          ${this._contactCard("Confidentialitate", "Date sensibile", "Nu trimite parole, tokenuri sau AWB-uri neanonimizate. Mascheaza datele personale din capturi.", "mdi:shield-lock-outline", "")}
        </section>
        <section class="cr-contact-note">
          <h3>Cand raportezi o problema</h3>
          <p>Ajuta mult sa mentionezi versiunea integrarii, curierul afectat, ce ai incercat deja, daca problema apare dupa restart si o captura din panel cu datele sensibile mascate.</p>
        </section>
      </main>
    `;
  }

  _contactCard(title, main, text, icon, href) {
    const body = `
      <div class="cr-contact-icon"><ha-icon icon="${icon}"></ha-icon></div>
      <div><span>${this._escape(title)}</span><strong>${this._escape(main)}</strong><p>${this._escape(text)}</p></div>
    `;
    if (!href) return `<article class="cr-contact-card">${body}</article>`;
    return `<a class="cr-contact-card is-link" href="${this._escape(href)}" target="${href.startsWith('mailto:') ? '_self' : '_blank'}" rel="noopener noreferrer">${body}</a>`;
  }

  _renderSettingsPage() {
    const busy = this._notificationTestBusy;
    const notificationSettings = this._getNotificationSettings();
    const notificationDiagnostics = this._getNotificationDiagnostics();
    return `
      <main class="cr-page">
        <div class="cr-page-title">
          <span>Setari</span>
          <h2>Instrumente si configurare</h2>
          <p>Acces rapid la helper-ele pentru metodele avansate, entitatile Home Assistant si configurarea notificarilor.</p>
        </div>

        ${this._exitButtonSettingsPanel()}

        <section class="cr-notification-settings">
          <div class="cr-section-head">
            <div>
              <span>Notificari</span>
              <h2>Setari notificari reale</h2>
              <p>Alege ce notificari se trimit si, optional, serviciul mobil Home Assistant. Daca serviciul lipseste sau da eroare, integrarea foloseste notificari persistente.</p>
            </div>
          </div>
          ${this._notificationSettingsForm(notificationSettings)}
          ${this._notificationSettingsMessage ? `<div class="cr-message">${this._escape(this._notificationSettingsMessage)}</div>` : ""}
        </section>

        <section class="cr-test-panel">
          <div class="cr-section-head">
            <div>
              <span>Notificari</span>
              <h2>Test notificari</h2>
              <p>Testeaza mecanismul de notificari fara colet activ. Testele folosesc AWB-uri fictive si nu apar in lista reala de colete.</p>
            </div>
          </div>
          <div class="cr-test-grid">
            ${this._notificationTestCard("Notificare simpla", "Trimite o notificare persistenta simpla, marcata clar ca test.", "mdi:bell-ring-outline", "send_test_notification", "simple", busy)}
            ${this._notificationTestCard("Simuleaza colet nou", "Genereaza o notificare de colet nou cu AWB fictiv TEST, fara date reale.", "mdi:package-variant-plus", "simulate_new_parcel_notification", "new", busy)}
            ${this._notificationTestCard("Simuleaza schimbare status", "Trece un colet fictiv prin mai multe statusuri si trimite notificarea aferenta.", "mdi:truck-fast-outline", "simulate_status_change_notification", "status", busy)}
            ${this._notificationTestCard("Reseteaza testele", "Sterge doar istoricul de test din storage. Istoricul coletelor reale nu este modificat.", "mdi:restore-alert", "reset_test_notification_history", "reset", busy)}
          </div>
          ${this._notificationTestMessage ? `<div class="cr-message">${this._escape(this._notificationTestMessage)}</div>` : ""}
        </section>

        ${this._courierDiagnosticPanel()}

        ${this._notificationDiagnosticPanel(notificationSettings, notificationDiagnostics)}

        <section class="cr-settings-grid cr-helper-grid">
          ${this._settingsCard("Helper Sameday", "Deschide pagina locala cu instructiuni si bookmarklet pentru extragerea refresh tokenului Sameday.", "mdi:truck-fast-outline", "sameday")}
          ${this._settingsCard("Helper Cargus", "Deschide pagina locala cu instructiuni si bookmarklet pentru extragerea refresh tokenului MyCargus.", "mdi:truck-check-outline", "cargus")}
        </section>
      </main>
    `;
  }



  _exitButtonSettingsPanel() {
    const settings = this._getExitButtonSettings();
    const checked = settings.enabled ? "checked" : "";
    const options = [
      ["/lovelace", "Overview / Lovelace"],
      ["/", "Pagina principala Home Assistant"],
      ["/config", "Setari Home Assistant"],
      ["/profile", "Profil utilizator"],
      ["custom", "URL custom"]
    ];
    return `
      <section class="cr-notification-settings cr-exit-settings">
        <div class="cr-section-head">
          <div>
            <span>Dashboard</span>
            <h2>Buton iesire panel</h2>
            <p>Util pentru telefon, modul kiosk sau cand bara laterala Home Assistant este ascunsa.</p>
          </div>
        </div>
        <form class="cr-notify-form" data-form="exit-button-settings">
          <div class="cr-notify-main">
            <label class="cr-switch-row">
              <input type="checkbox" name="exit_button_enabled" ${checked}>
              <span><strong>Afiseaza buton iesire</strong><small>Butonul apare in header-ul panelului si te duce rapid catre pagina aleasa.</small></span>
            </label>
            <label class="cr-field-row">
              <span>Destinatie</span>
              <select name="exit_button_target">
                ${options.map(([value, label]) => `<option value="${this._escape(value)}" ${settings.target === value ? "selected" : ""}>${this._escape(label)}</option>`).join("")}
              </select>
              <small>Alege unde duce butonul. Pentru dashboard custom, foloseste URL custom.</small>
            </label>
          </div>
          <div class="cr-notify-main cr-exit-extra">
            <label class="cr-field-row">
              <span>Text buton</span>
              <input name="exit_button_label" value="${this._escape(settings.label || "Iesire")}" placeholder="Iesire" autocomplete="off">
              <small>Text scurt, vizibil mai ales pe desktop. Pe telefon ramane si iconita.</small>
            </label>
            <label class="cr-field-row">
              <span>URL custom</span>
              <input name="exit_button_custom_url" value="${this._escape(settings.customUrl || "")}" placeholder="/lovelace/default_view" autocomplete="off">
              <small>Se foloseste doar cand destinatia este URL custom.</small>
            </label>
          </div>
          <div class="cr-form-actions">
            <button type="submit">Salveaza setarile</button>
          </div>
        </form>
        ${this._exitSettingsMessage ? `<div class="cr-message">${this._escape(this._exitSettingsMessage)}</div>` : ""}
      </section>
    `;
  }

  _courierDiagnosticPanel() {
    const diagnostics = this._getCourierDiagnostics();
    const total = diagnostics.reduce((sum, item) => sum + Number(item.total || 0), 0);
    return `
      <section class="cr-diagnostic-panel">
        <div class="cr-section-head">
          <div>
            <span>Diagnostic</span>
            <h2>Diagnostic curieri</h2>
            <p>Verificare rapida pentru curierii detectati, numarul de colete si eventualele erori raportate de coordonator.</p>
          </div>
        </div>
        ${diagnostics.length ? `
          <div class="cr-courier-diagnostic-grid">
            ${diagnostics.map((item) => this._courierDiagnosticCard(item)).join("")}
          </div>
          <p class="cr-help">Total colete detectate in panel: ${this._escape(total)}. Datele de diagnostic nu includ tokenuri, parole sau AWB-uri complete.</p>
        ` : `<div class="cr-empty">Nu exista inca date de diagnostic pentru curieri. Dupa prima actualizare vor aparea aici.</div>`}
      </section>
    `;
  }

  _courierDiagnosticCard(item) {
    const hasError = String(item.error || "").trim();
    return `
      <article class="cr-courier-diagnostic-card ${hasError ? "is-bad" : "is-good"}">
        <div class="cr-courier-diagnostic-head">
          <div><span>Curier</span><strong>${this._escape(item.courier)}</strong></div>
          <ha-icon icon="${hasError ? "mdi:alert-circle-outline" : "mdi:check-circle-outline"}"></ha-icon>
        </div>
        <div class="cr-mini-stats">
          <div><span>Total</span><strong>${this._escape(item.total)}</strong></div>
          <div><span>Active</span><strong>${this._escape(item.active)}</strong></div>
          <div><span>Livrate</span><strong>${this._escape(item.delivered)}</strong></div>
          <div><span>Probleme</span><strong>${this._escape(item.problems)}</strong></div>
        </div>
        <small>${hasError ? this._escape(item.error) : `Ultima actualizare: ${this._escape(this._formatDate(item.lastUpdate))}`}</small>
      </article>
    `;
  }

  _notificationDiagnosticPanel(settings, diagnostics) {
    const history = this._getNotificationHistoryDiagnostics();
    const display = (value) => {
      const clean = String(value || "").trim();
      return clean && clean !== "-" ? clean : "-";
    };
    const service = settings.notifyService ? settings.notifyService : "persistent_notification";
    const active = settings.notificationsEnabled ? "active" : "inactive";
    return `
      <section class="cr-diagnostic-panel">
        <div class="cr-section-head">
          <div>
            <span>Diagnostic</span>
            <h2>Diagnostic notificari</h2>
            <p>Informatii rapide pentru verificarea serviciului selectat, a ultimei notificari trimise si a eventualului fallback.</p>
          </div>
        </div>
        <div class="cr-diagnostic-grid">
          ${this._diagnosticTile("Notificari", active, settings.notificationsEnabled ? "Notificarile reale sunt active." : "Notificarile reale sunt oprite.")}
          ${this._diagnosticTile("Serviciu selectat", service, settings.notifyService ? "Serviciu notify ales din Home Assistant." : "Fallback pe notificare persistenta.")}
          ${this._diagnosticTile("Ultima notificare", display(diagnostics.lastAt), display(diagnostics.lastTitle))}
          ${this._diagnosticTile("Tinta folosita", display(diagnostics.lastTarget), display(diagnostics.lastResult))}
          ${this._diagnosticTile("Ultima eroare", display(diagnostics.lastError), display(diagnostics.lastError) === "-" ? "Nu exista eroare salvata." : "Verifica serviciul notify selectat.")}
          ${this._diagnosticTile("Istoric total", display(history.totalStates), "Numar stari salvate pentru evitarea notificarilor duplicate.")}
          ${this._diagnosticTile("Istoric real", display(history.realStates), "Stari aferente coletelor reale.")}
          ${this._diagnosticTile("Istoric test", display(history.testStates), "Stari folosite doar de testele de notificari.")}
          ${this._diagnosticTile("Ultima curatare", this._formatDate(display(history.lastCleanupAt)), "Curatarea sterge doar stari vechi, nu colete reale curente.")}
          ${this._diagnosticTile("Curatate", display(history.lastCleanupRemoved), "Cate stari au fost sterse la ultima curatare.")}
        </div>
      </section>
    `;
  }

  _diagnosticTile(label, value, help) {
    return `
      <article class="cr-diagnostic-tile">
        <span>${this._escape(label)}</span>
        <strong>${this._escape(value)}</strong>
        <small>${this._escape(help || "")}</small>
      </article>
    `;
  }

  _notificationSettingsForm(settings) {
    const checked = (value) => value ? "checked" : "";
    const disabled = this._notificationSettingsBusy ? "disabled" : "";
    return `
      <form class="cr-notify-form" data-form="notification-settings">
        <div class="cr-notify-main">
          <label class="cr-switch-row">
            <input type="checkbox" name="notifications_enabled" ${checked(settings.notificationsEnabled)} ${disabled}>
            <span><strong>Notificari active</strong><small>Opreste toate notificarile reale fara sa afecteze testele sau memorarea statusurilor.</small></span>
          </label>
          <label class="cr-field-row">
            <span>Serviciu notificare mobil</span>
            <select name="notify_service" ${disabled}>
              ${this._getDetectedNotifyServices(settings.notifyService).map((option) => `
                <option value="${this._escape(option.value)}" ${String(settings.notifyService || "") === option.value ? "selected" : ""}>${this._escape(option.label)}</option>
              `).join("")}
            </select>
            <small>Alege un serviciu detectat automat sau lasa prima optiune pentru notificari persistente in Home Assistant.</small>
          </label>
        </div>
        <div class="cr-check-grid">
          ${this._notificationCheckbox("Colet nou", "notify_new_parcel", settings.notifyNewParcel, "Apare un AWB nou dupa prima scanare.", disabled)}
          ${this._notificationCheckbox("Schimbare status", "notify_status_change", settings.notifyStatusChange, "Statusuri obisnuite, daca nu intra intr-o categorie importanta.", disabled)}
          ${this._notificationCheckbox("In livrare", "notify_out_for_delivery", settings.notifyOutForDelivery, "Coletul intra in livrare catre destinatar.", disabled)}
          ${this._notificationCheckbox("Ridicare", "notify_pickup", settings.notifyPickup, "Colet disponibil la locker sau punct de ridicare.", disabled)}
          ${this._notificationCheckbox("Livrat", "notify_delivered", settings.notifyDelivered, "Coletul a fost livrat.", disabled)}
          ${this._notificationCheckbox("Probleme", "notify_problems", settings.notifyProblems, "Livrare esuata sau status cu problema.", disabled)}
          ${this._notificationCheckbox("Retur", "notify_returned", settings.notifyReturned, "Coletul este returnat sau blocat pe retur.", disabled)}
        </div>
        <div class="cr-form-actions">
          <button type="submit" ${disabled}>${this._notificationSettingsBusy ? "Se salveaza..." : "Salveaza setarile"}</button>
        </div>
      </form>
    `;
  }

  _notificationCheckbox(title, name, value, help, disabled) {
    return `
      <label class="cr-check-card">
        <input type="checkbox" name="${this._escape(name)}" ${value ? "checked" : ""} ${disabled}>
        <span><strong>${this._escape(title)}</strong><small>${this._escape(help)}</small></span>
      </label>
    `;
  }


  _notificationTestCard(title, text, icon, service, key, busy) {
    const isBusy = busy === key;
    return `
      <article class="cr-settings-card cr-test-card">
        <div class="cr-settings-icon"><ha-icon icon="${icon}"></ha-icon></div>
        <div><h3>${this._escape(title)}</h3><p>${this._escape(text)}</p></div>
        <button type="button" data-notification-test="${service}" data-busy-key="${key}" ${busy ? "disabled" : ""}>${isBusy ? "Se executa..." : "Ruleaza"}</button>
      </article>
    `;
  }

  _settingsCard(title, text, icon, helper) {
    return `
      <article class="cr-settings-card">
        <div class="cr-settings-icon"><ha-icon icon="${icon}"></ha-icon></div>
        <div><h3>${this._escape(title)}</h3><p>${this._escape(text)}</p></div>
        <button type="button" data-helper="${helper}">Deschide helper</button>
      </article>
    `;
  }

  _kpi(label, value, sub, icon, cls) {
    return `
      <article class="cr-kpi ${cls}">
        <ha-icon icon="${icon}"></ha-icon>
        <span>${this._escape(label)}</span>
        <strong>${this._escape(value)}</strong>
        <small>${this._escape(sub)}</small>
      </article>
    `;
  }

  _licenseTile(label, value, icon) {
    return `
      <article class="cr-license-tile">
        <ha-icon icon="${icon}"></ha-icon>
        <span>${this._escape(label)}</span>
        <strong>${this._escape(value || "-")}</strong>
      </article>
    `;
  }

  _bindEvents() {
    for (const tab of this.shadowRoot.querySelectorAll("[data-tab]")) {
      tab.addEventListener("click", () => this._setTab(tab.getAttribute("data-tab")));
    }
    this._bindParcelCardEvents();
    for (const helper of this.shadowRoot.querySelectorAll("[data-helper]")) {
      helper.addEventListener("click", () => this._openHelper(helper.getAttribute("data-helper")));
    }
    for (const testButton of this.shadowRoot.querySelectorAll("[data-notification-test]")) {
      testButton.addEventListener("click", () => this._callNotificationTest(
        testButton.getAttribute("data-notification-test"),
        testButton.getAttribute("data-busy-key"),
        "Testul a fost executat. Verifica serviciul configurat sau notificarile persistente din Home Assistant."
      ));
    }

    const exitButton = this.shadowRoot.querySelector("[data-action='panel-exit']");
    if (exitButton) exitButton.addEventListener("click", () => this._navigateToExitTarget());

    const exitForm = this.shadowRoot.querySelector("form[data-form='exit-button-settings']");
    if (exitForm) {
      exitForm.addEventListener("submit", (event) => {
        event.preventDefault();
        this._saveExitButtonSettings(exitForm);
      });
    }

    const notificationForm = this.shadowRoot.querySelector("form[data-form='notification-settings']");
    if (notificationForm) {
      notificationForm.addEventListener("submit", (event) => {
        event.preventDefault();
        this._saveNotificationSettings(notificationForm);
      });
    }
    const sortSelect = this.shadowRoot.querySelector(".cr-sort select");
    if (sortSelect) sortSelect.addEventListener("change", () => {
      this._parcelSort = sortSelect.value || "newest";
      this._savePreference("parcel_sort", this._parcelSort);
      this._updateParcelListOnly();
    });
    const searchInput = this.shadowRoot.querySelector(".cr-search input");
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        this._parcelSearch = searchInput.value || "";
        if (this._searchTimer) window.clearTimeout(this._searchTimer);
        this._searchTimer = window.setTimeout(() => {
          this._searchTimer = null;
          this._updateParcelListOnly();
        }, 120);
      });
      searchInput.addEventListener("blur", () => {
        window.setTimeout(() => this._flushPendingHassRefresh(), 0);
      });
    }
    for (const interactive of this.shadowRoot.querySelectorAll("form[data-form='license'], form[data-form='notification-settings'], form[data-form='exit-button-settings'], .cr-toolbar")) {
      interactive.addEventListener("focusout", () => {
        window.setTimeout(() => {
          if (!this._isInteractiveControlActive()) this._flushPendingHassRefresh();
        }, 0);
      });
    }

    const form = this.shadowRoot.querySelector("form[data-form='license']");
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const value = String(new FormData(form).get("license_key") || "").trim();
        this._licenseDraft = value;
        this._applyLicense(value);
      });
      const input = form.querySelector("input[name='license_key']");
      if (input) input.addEventListener("input", () => { this._licenseDraft = input.value; });
    }
    const refresh = this.shadowRoot.querySelector("[data-action='license-refresh']");
    if (refresh) refresh.addEventListener("click", () => this._refreshLicenseEntities(true));
    const backdrop = this.shadowRoot.querySelector(".drawer-backdrop");
    if (backdrop) backdrop.addEventListener("click", (event) => { if (event.target === backdrop) this._closeDetails(); });
    const closeButton = this.shadowRoot.querySelector(".js-close-details");
    if (closeButton) closeButton.addEventListener("click", () => this._closeDetails());
    const moreInfoButton = this.shadowRoot.querySelector(".js-more-info");
    if (moreInfoButton) moreInfoButton.addEventListener("click", () => this._openEntity(moreInfoButton.getAttribute("data-entity")));
  }

  _eventTime(event) {
    return event?.event_time || event?.time || event?.date || event?.timestamp || null;
  }

  _eventStatus(event) {
    return event?.status || event?.name || event?.description || "Eveniment";
  }

  _eventLocation(event) {
    return event?.location || event?.city || event?.locality || "";
  }

  _eventNormalizedStatus(event) {
    return event?.normalized_status || event?.normalizedStatus || "";
  }

  _detailItem(label, value, formatter = null) {
    const rawValue = value === null || value === undefined || value === "" ? "Necunoscut" : value;
    const displayValue = formatter ? formatter(rawValue) : rawValue;
    return `
      <div class="detail-item">
        <div class="detail-label">${this._escape(label)}</div>
        <div class="detail-value">${this._escape(displayValue)}</div>
      </div>
    `;
  }

  _detailDrawer(parcel) {
    const events = [...(parcel.events || [])].sort((a, b) => {
      const aTime = new Date(this._eventTime(a) || 0).getTime();
      const bTime = new Date(this._eventTime(b) || 0).getTime();
      return aTime - bTime;
    });
    const className = this._statusClass(parcel);
    return `
      <div class="drawer-backdrop">
        <aside class="drawer" role="dialog" aria-label="Detalii colet">
          <div class="drawer-head">
            <div><div class="drawer-title">${this._escape(parcel.awbShort)}</div><div class="drawer-subtitle">${this._escape(parcel.courier)} · ${this._escape(parcel.sender || "Necunoscut")}</div></div>
            <div class="drawer-actions"><button class="icon-button js-more-info" data-entity="${this._escape(parcel.entityId)}" title="Deschide entitatea Home Assistant"><ha-icon icon="mdi:open-in-new"></ha-icon></button><button class="icon-button js-close-details" title="Inchide">×</button></div>
          </div>
          <div class="drawer-body">
            <div class="detail-section">
              <div class="top"><div><h2>Rezumat colet</h2><div class="drawer-subtitle mono">AWB: ${this._escape(parcel.awb)}</div></div><div class="cr-pill ${className}">${this._escape(parcel.status)}</div></div>
              <div class="detail-grid">
                ${this._detailItem("Curier", parcel.courier)}
                ${this._detailItem("Directie", parcel.direction)}
                ${this._detailItem("Expeditor", parcel.sender)}
                ${this._detailItem("Destinatar", parcel.recipient)}
                ${this._detailItem("Status original", parcel.originalStatus)}
                ${this._detailItem("Status normalizat", parcel.status)}
                ${this._detailItem("Locatie", parcel.location || parcel.lockerName)}
                ${this._detailItem("Locker / punct ridicare", parcel.lockerName)}
                ${this._detailItem("Ramburs", parcel.cod, (value) => this._formatMoney(value))}
                ${this._detailItem("PIN expira", parcel.pinExpiration, (value) => this._formatDate(value))}
                ${this._detailItem("Estimare livrare", parcel.attributes.estimated_delivery, (value) => this._formatDate(value))}
                ${this._detailItem("Livrat la", parcel.deliveredAt, (value) => this._formatDate(value))}
                ${this._detailItem("Ultima actualizare", parcel.lastUpdate, (value) => this._formatDate(value))}
                ${this._detailItem("Tip livrare", parcel.attributes.delivery_type || parcel.attributes.deliveryType)}
              </div>
            </div>
            <div class="detail-section"><h2>Istoric livrare</h2>${events.length ? this._timeline(events) : `<div class="cr-empty">Nu exista evenimente detaliate pentru acest colet.</div>`}</div>
          </div>
        </aside>
      </div>
    `;
  }

  _timeline(events) {
    return `<ol class="timeline">${events.map((event) => {
      const status = this._eventStatus(event);
      const time = this._eventTime(event);
      const location = this._eventLocation(event);
      const normalized = this._eventNormalizedStatus(event);
      return `<li class="timeline-item"><span class="timeline-dot"></span><div class="timeline-status">${this._escape(status)}</div><div class="timeline-meta">${this._escape(this._formatDate(time))}${location ? ` · ${this._escape(location)}` : ""}</div>${normalized ? `<div class="timeline-normalized">${this._escape(normalized)}</div>` : ""}</li>`;
    }).join("")}</ol>`;
  }

  _parcelCard(parcel) {
    const className = this._statusClass(parcel);
    const title = parcel.sender && parcel.sender !== "Necunoscut" ? parcel.sender : parcel.friendlyName;
    const where = parcel.location || parcel.lockerName || "Necunoscut";
    return `
      <article class="cr-parcel-card" data-entity="${this._escape(parcel.entityId)}">
        <div class="cr-parcel-top"><div><span>${this._escape(parcel.courier)}</span><strong>${this._escape(parcel.awbShort)}</strong></div><div class="cr-pill ${className}">${this._escape(parcel.status)}</div></div>
        <h3>${this._escape(title)}</h3>
        <div class="cr-lines">
          <div><span>Locatie</span><strong>${this._escape(where)}</strong></div>
          <div><span>Ramburs</span><strong>${this._formatMoney(parcel.cod)}</strong></div>
          <div><span>PIN expira</span><strong>${this._escape(this._formatDate(parcel.pinExpiration))}</strong></div>
          <div><span>Ultima actualizare</span><strong>${this._escape(this._formatDate(parcel.lastUpdate))}</strong></div>
        </div>
      </article>
    `;
  }

  _styles() {
    return `
      :host{display:block;min-height:100vh;background:var(--primary-background-color);color:var(--primary-text-color);--cr-bg:linear-gradient(135deg,rgba(18,42,68,.98),rgba(18,151,171,.92));--cr-card:color-mix(in srgb,var(--card-background-color) 92%,var(--primary-color) 8%);--cr-line:color-mix(in srgb,var(--divider-color) 78%,var(--primary-color) 22%);--cr-muted:var(--secondary-text-color);--cr-accent:var(--primary-color);--cr-shadow:0 18px 46px rgba(0,0,0,.18)}
      .cr-panel{max-width:1760px;margin:0 auto;padding:30px 28px 80px;box-sizing:border-box}
      .cr-hero{display:grid;grid-template-columns:minmax(0,1fr) 248px;gap:24px;align-items:stretch;margin-bottom:22px}
      .cr-hero-main{position:relative;overflow:hidden;border-radius:28px;min-height:210px;padding:34px 36px;background:var(--cr-bg);color:#fff;box-shadow:var(--cr-shadow);display:flex;align-items:flex-start;gap:22px}
      .cr-hero-main:before,.cr-hero-main:after{content:"";position:absolute;border-radius:999px;background:rgba(255,255,255,.18);pointer-events:none;z-index:0}.cr-hero-main:before{width:250px;height:250px;right:60px;top:-150px}.cr-hero-main:after{width:190px;height:190px;right:250px;bottom:-120px}.cr-hero-main>*{position:relative;z-index:1}
      .cr-logo{width:74px;height:74px;border-radius:22px;background:rgba(255,255,255,.78);display:flex;align-items:center;justify-content:center;box-shadow:inset 0 0 0 1px rgba(255,255,255,.28),0 10px 28px rgba(0,0,0,.12);z-index:1;overflow:hidden}.cr-logo img{width:100%;height:100%;object-fit:cover;display:block;border-radius:22px}
      .cr-hero h1{position:relative;z-index:1;font-size:42px;line-height:1;margin:6px 0 10px;font-weight:900;letter-spacing:-.04em}.cr-hero p{position:relative;z-index:1;margin:0;color:rgba(255,255,255,.86);font-weight:700}
      .cr-exit-button{position:absolute;right:24px;top:22px;border:1px solid rgba(255,255,255,.28);background:rgba(7,39,58,.45);color:#fff;border-radius:999px;min-height:42px;padding:0 16px;display:inline-flex;align-items:center;gap:8px;font-weight:950;cursor:pointer;backdrop-filter:blur(8px);box-shadow:0 12px 28px rgba(0,0,0,.16)}.cr-exit-button ha-icon{width:20px;height:20px}.cr-hero-badge{position:absolute;right:24px;bottom:22px;background:rgba(7,39,58,.72);border:1px solid rgba(255,255,255,.24);border-radius:18px;padding:8px 12px 8px 8px;display:grid;grid-template-columns:36px auto;grid-template-rows:auto auto;column-gap:9px;row-gap:1px;align-items:center;text-transform:uppercase;font-weight:900;letter-spacing:.08em;overflow:hidden;box-shadow:0 10px 24px rgba(0,0,0,.14);backdrop-filter:blur(8px);color:#fff;text-decoration:none}.cr-hero-badge img{grid-row:1/3;width:36px;height:36px;border-radius:10px;object-fit:cover;display:block;box-shadow:0 4px 12px rgba(0,0,0,.18)}.cr-hero-badge span{line-height:1.05}.cr-hero-badge small{font-size:10px;opacity:.78;line-height:1}
      .cr-hero-side{display:grid;grid-template-columns:1fr;gap:14px}.cr-side-card{border:1px solid var(--cr-line);border-radius:22px;background:var(--cr-card);padding:20px 22px;box-shadow:0 10px 28px rgba(0,0,0,.08)}.cr-side-card span{display:block;color:var(--cr-muted);font-weight:800;font-size:12px}.cr-side-card strong{display:block;font-size:30px;margin-top:9px;line-height:1;font-weight:900}
      .cr-tabs-shell{border:1px solid var(--cr-line);border-radius:24px;background:var(--cr-card);padding:9px;margin-bottom:18px;box-shadow:0 10px 28px rgba(0,0,0,.08)}.cr-tabs{display:flex;gap:8px;overflow-x:auto;scrollbar-width:thin}.cr-tab{border:0;border-radius:18px;background:transparent;color:var(--cr-muted);min-height:44px;padding:0 20px;display:flex;align-items:center;justify-content:center;gap:8px;font-weight:900;cursor:pointer;white-space:nowrap;min-width:136px}.cr-tab ha-icon{width:20px;height:20px}.cr-tab.active{background:var(--cr-accent);color:var(--text-primary-color,#fff);box-shadow:0 10px 24px color-mix(in srgb,var(--cr-accent) 30%,transparent)}
      .cr-page{border:1px solid var(--cr-line);border-radius:28px;background:var(--cr-card);padding:24px;box-shadow:0 10px 28px rgba(0,0,0,.08)}.cr-page-title{margin-bottom:22px;border-bottom:1px solid var(--cr-line);padding-bottom:16px}.cr-page-title span,.cr-section-head span{display:block;color:#6ab7ff;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:900}.cr-page-title h2{margin:6px 0;font-size:26px}.cr-page-title p,.cr-section-head p{margin:0;color:var(--cr-muted)}
      .cr-kpis,.cr-license-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}.cr-kpi,.cr-license-tile{position:relative;overflow:hidden;border:1px solid var(--cr-line);border-radius:20px;background:color-mix(in srgb,var(--card-background-color) 88%,var(--primary-color) 12%);padding:18px}.cr-kpi:after,.cr-license-tile:after{content:"";position:absolute;right:-28px;top:-36px;width:94px;height:94px;border-radius:999px;background:rgba(255,255,255,.06)}.cr-kpi ha-icon,.cr-license-tile ha-icon{color:#00c5e8}.cr-kpi span,.cr-license-tile span{display:block;margin-top:12px;color:var(--cr-muted);font-weight:900;font-size:12px}.cr-kpi strong,.cr-license-tile strong{display:block;margin-top:6px;font-size:28px;font-weight:950;word-break:break-word}.cr-kpi small{display:block;margin-top:4px;color:var(--cr-muted);font-weight:800}
      .cr-section{margin-top:16px}.cr-section-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.cr-section-head h3{margin:4px 0 0;font-size:22px}.cr-section-head button,.cr-action-row button,.cr-settings-card button,.cr-form-card button{border:0;border-radius:999px;background:var(--cr-accent);color:var(--text-primary-color,#fff);font-weight:900;padding:12px 18px;cursor:pointer;display:inline-flex;align-items:center;gap:8px}.cr-section-head button:hover,.cr-action-row button:hover,.cr-settings-card button:hover,.cr-form-card button:hover{filter:brightness(1.06)}button:disabled{opacity:.62;cursor:wait}
      .cr-toolbar{display:grid;grid-template-columns:auto minmax(240px,1fr) auto;gap:12px;align-items:center;margin:-4px 0 18px}.cr-toolbar-info{border:1px solid var(--cr-line);border-radius:18px;padding:11px 14px;background:color-mix(in srgb,var(--card-background-color) 90%,var(--primary-color) 10%);white-space:nowrap}.cr-toolbar-info strong{font-size:20px;margin-right:6px}.cr-toolbar-info span{color:var(--cr-muted);font-weight:800}.cr-search,.cr-sort{border:1px solid var(--cr-line);border-radius:18px;background:var(--card-background-color);min-height:48px;display:flex;align-items:center;gap:9px;padding:0 13px;box-sizing:border-box}.cr-search ha-icon{color:var(--cr-muted)}.cr-search input,.cr-sort select{border:0;outline:0;background:transparent;color:var(--primary-text-color);font-weight:800;width:100%;min-height:42px}.cr-sort span{color:var(--cr-muted);font-weight:900;font-size:12px;white-space:nowrap}.cr-sort select{cursor:pointer;min-width:190px;background:var(--card-background-color);color:var(--primary-text-color);color-scheme:dark}.cr-sort select option{background:var(--card-background-color);color:var(--primary-text-color)}
      .cr-grouped-list{display:grid;gap:20px}.cr-parcel-group{display:grid;gap:12px}.cr-group-header{display:flex;align-items:flex-end;justify-content:space-between;gap:14px;border:1px solid var(--cr-line);border-radius:20px;padding:14px 16px;background:color-mix(in srgb,var(--card-background-color) 92%,var(--primary-color) 8%)}.cr-group-header span{display:block;color:#6ab7ff;text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:950}.cr-group-header h3{margin:5px 0 0;font-size:20px}.cr-group-header strong{color:var(--cr-muted);font-weight:950;white-space:nowrap}
      .cr-card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}.cr-parcel-card{border:1px solid var(--cr-line);border-radius:22px;background:color-mix(in srgb,var(--card-background-color) 90%,var(--primary-color) 10%);padding:18px;cursor:pointer;transition:transform .12s ease,border-color .12s ease}.cr-parcel-card:hover{transform:translateY(-2px);border-color:var(--cr-accent)}.cr-parcel-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.cr-parcel-top span{display:block;color:var(--cr-muted);font-weight:800;font-size:12px}.cr-parcel-top strong{display:block;margin-top:4px;font-size:16px}.cr-parcel-card h3{margin:16px 0 12px;font-size:18px}.cr-lines{display:grid;gap:0}.cr-lines div{display:flex;justify-content:space-between;gap:14px;border-top:1px solid var(--cr-line);padding:9px 0}.cr-lines span{color:var(--cr-muted)}.cr-lines strong{text-align:right;word-break:break-word}.cr-pill{border-radius:999px;padding:6px 10px;font-weight:900;font-size:12px;white-space:nowrap}.is-good{background:rgba(31,178,103,.16)!important;color:#28c778!important}.is-info{background:rgba(3,169,244,.16)!important;color:#26b6e8!important}.is-bad{background:rgba(244,67,54,.16)!important;color:#ef5350!important}.is-warn{background:rgba(255,170,0,.16)!important;color:#ffad33!important}.is-neutral{background:rgba(130,145,165,.16)!important;color:var(--cr-muted)!important}
      .cr-empty{padding:28px;text-align:center;color:var(--cr-muted);border:1px dashed var(--cr-line);border-radius:20px}.cr-license-hero{display:flex;justify-content:space-between;align-items:center;gap:18px;border:1px solid var(--cr-line);border-radius:24px;padding:24px;margin-bottom:16px}.cr-license-hero span{color:var(--cr-muted);font-weight:900}.cr-license-hero strong{display:block;font-size:34px;margin:8px 0}.cr-license-hero p{margin:0;color:var(--cr-muted)}.cr-license-hero ha-icon{width:56px;height:56px}.cr-license-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.cr-license-tile strong{font-size:17px}.cr-form-card,.cr-action-row,.cr-settings-card{border:1px solid var(--cr-line);border-radius:22px;background:color-mix(in srgb,var(--card-background-color) 90%,var(--primary-color) 10%);padding:20px;margin-top:14px}.cr-form-card form{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:end}.cr-form-card label span{display:block;color:var(--cr-muted);font-weight:900;font-size:12px;margin-bottom:7px}.cr-form-card input{width:100%;box-sizing:border-box;border:1px solid var(--cr-line);background:var(--card-background-color);color:var(--primary-text-color);border-radius:16px;min-height:46px;padding:0 14px;font-weight:900}.cr-help{margin:10px 0 0;color:var(--cr-muted);font-size:12px}.cr-message{margin-top:12px;border-radius:16px;padding:12px 14px;background:rgba(3,169,244,.12);color:var(--primary-text-color);font-weight:800}.cr-action-row{display:flex;align-items:center;justify-content:space-between;gap:16px}.cr-action-row h3{margin:0 0 5px}.cr-action-row p{margin:0;color:var(--cr-muted)}.cr-action-row small{display:block;margin-top:8px;color:var(--cr-muted)}.cr-license-support{display:flex;gap:18px;align-items:flex-start;border:1px solid var(--cr-line);border-radius:24px;background:var(--cr-card);padding:22px;margin-top:18px;box-shadow:0 10px 28px rgba(0,0,0,.06)}.cr-license-support-icon{width:54px;height:54px;border-radius:20px;background:color-mix(in srgb,var(--cr-accent) 14%,var(--card-background-color));display:grid;place-items:center;flex:0 0 auto}.cr-license-support-icon ha-icon{width:26px;height:26px;color:var(--cr-accent)}.cr-license-support h3{margin:0 0 8px;font-size:22px}.cr-license-support p{margin:0 0 8px;color:var(--primary-text-color);font-weight:750;line-height:1.45}.cr-license-support a{display:inline-flex;align-items:center;gap:8px;margin-top:10px;border-radius:999px;background:#ffd400;color:#111827;text-decoration:none;font-weight:950;padding:12px 18px;box-shadow:0 12px 26px rgba(0,0,0,.16)}.cr-license-support small{display:block;margin-top:12px;color:var(--cr-muted);font-weight:850}.cr-contact-hero{display:flex;gap:18px;align-items:center;border:1px solid var(--cr-line);border-radius:24px;background:color-mix(in srgb,var(--card-background-color) 88%,var(--primary-color) 12%);padding:22px;margin-bottom:18px}.cr-contact-hero img{width:74px;height:74px;border-radius:22px;object-fit:cover;box-shadow:0 12px 26px rgba(0,0,0,.18)}.cr-contact-hero span,.cr-contact-card span{display:block;color:#6ab7ff;text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:950}.cr-contact-hero h3{margin:5px 0 8px;font-size:24px}.cr-contact-hero p,.cr-contact-card p,.cr-contact-note p{margin:0;color:var(--cr-muted);line-height:1.45}.cr-contact-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.cr-contact-card{border:1px solid var(--cr-line);border-radius:22px;background:var(--cr-card);padding:18px;display:flex;gap:14px;text-decoration:none;color:var(--primary-text-color);min-width:0}.cr-contact-card.is-link:hover{border-color:color-mix(in srgb,var(--cr-accent) 70%,var(--cr-line));transform:translateY(-1px)}.cr-contact-card strong{display:block;margin:6px 0 6px;font-size:18px;word-break:break-word}.cr-contact-icon{width:46px;height:46px;border-radius:18px;background:color-mix(in srgb,var(--cr-accent) 14%,var(--card-background-color));display:grid;place-items:center;flex:0 0 auto}.cr-contact-icon ha-icon{color:var(--cr-accent);width:24px;height:24px}.cr-contact-note{border:1px solid var(--cr-line);border-radius:22px;background:color-mix(in srgb,var(--card-background-color) 92%,var(--primary-color) 8%);padding:20px;margin-top:18px}.cr-contact-note h3{margin:0 0 8px}.cr-settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.cr-helper-grid{margin-top:18px}.cr-notification-settings{margin-bottom:18px}.cr-notify-form{border:1px solid var(--cr-line);border-radius:22px;background:color-mix(in srgb,var(--card-background-color) 90%,var(--primary-color) 10%);padding:20px}.cr-notify-main{display:grid;grid-template-columns:1fr 1.2fr;gap:14px;margin-bottom:14px}.cr-switch-row,.cr-field-row,.cr-check-card{border:1px solid var(--cr-line);border-radius:18px;background:var(--card-background-color);padding:14px;box-sizing:border-box}.cr-switch-row{display:flex;gap:12px;align-items:flex-start}.cr-switch-row input,.cr-check-card input{margin-top:3px;accent-color:var(--cr-accent)}.cr-switch-row strong,.cr-check-card strong{display:block}.cr-switch-row small,.cr-field-row small,.cr-check-card small{display:block;color:var(--cr-muted);font-size:12px;margin-top:5px;line-height:1.35}.cr-field-row span{display:block;color:var(--cr-muted);font-weight:900;font-size:12px;margin-bottom:7px}.cr-field-row input,.cr-field-row select{width:100%;box-sizing:border-box;border:1px solid var(--cr-line);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:14px;min-height:42px;padding:0 12px;font-weight:800}.cr-field-row select{cursor:pointer;color-scheme:dark}.cr-field-row select option{background:var(--card-background-color);color:var(--primary-text-color)}.cr-check-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.cr-check-card{display:flex;gap:12px;align-items:flex-start}.cr-form-actions{display:flex;justify-content:flex-end;margin-top:14px}.cr-form-actions button{border:0;border-radius:999px;background:var(--cr-accent);color:#fff;font-weight:950;padding:12px 18px;cursor:pointer}.cr-test-panel,.cr-diagnostic-panel{margin-top:18px}.cr-diagnostic-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.cr-diagnostic-tile{border:1px solid var(--cr-line);border-radius:18px;background:var(--card-background-color);padding:14px;min-width:0}.cr-diagnostic-tile span{display:block;color:var(--cr-muted);font-weight:900;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.cr-diagnostic-tile strong{display:block;margin-top:7px;font-size:15px;word-break:break-word}.cr-diagnostic-tile small{display:block;margin-top:6px;color:var(--cr-muted);font-size:12px;line-height:1.35}.cr-courier-diagnostic-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.cr-courier-diagnostic-card{border:1px solid var(--cr-line);border-radius:20px;background:var(--card-background-color);padding:16px}.cr-courier-diagnostic-card.is-good{border-color:rgba(46,204,113,.45)}.cr-courier-diagnostic-card.is-bad{border-color:rgba(255,82,82,.65)}.cr-courier-diagnostic-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.cr-courier-diagnostic-head span{display:block;color:var(--cr-muted);font-weight:900;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.cr-courier-diagnostic-head strong{display:block;margin-top:5px;font-size:18px}.cr-courier-diagnostic-head ha-icon{color:var(--cr-accent)}.cr-courier-diagnostic-card.is-bad .cr-courier-diagnostic-head ha-icon{color:#ff5252}.cr-mini-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:14px 0}.cr-mini-stats div{border:1px solid var(--cr-line);border-radius:14px;padding:9px;background:color-mix(in srgb,var(--card-background-color) 90%,var(--primary-color) 10%)}.cr-mini-stats span{display:block;color:var(--cr-muted);font-size:11px;font-weight:800}.cr-mini-stats strong{display:block;margin-top:3px}.cr-courier-diagnostic-card small{display:block;color:var(--cr-muted);line-height:1.35}.cr-test-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.cr-settings-card{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:16px;margin-top:0}.cr-settings-icon{width:54px;height:54px;border-radius:18px;background:rgba(3,169,244,.12);display:flex;align-items:center;justify-content:center}.cr-settings-icon ha-icon{color:#00c5e8}.cr-settings-card h3{margin:0 0 6px}.cr-settings-card p{margin:0;color:var(--cr-muted)}
      .drawer-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.48);z-index:10;display:flex;justify-content:flex-end}.drawer{width:min(620px,calc(100vw - 22px));height:100vh;background:var(--card-background-color);border-left:1px solid var(--cr-line);box-shadow:-10px 0 28px rgba(0,0,0,.22);overflow:auto}.drawer-head{position:sticky;top:0;z-index:1;background:var(--card-background-color);border-bottom:1px solid var(--cr-line);padding:18px 20px;display:flex;align-items:center;justify-content:space-between;gap:14px}.drawer-title{font-size:20px;font-weight:900}.drawer-subtitle{color:var(--cr-muted);font-size:13px;margin-top:4px}.drawer-actions{display:flex;gap:8px}.icon-button{border:1px solid var(--cr-line);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:999px;min-width:38px;height:38px;cursor:pointer;font-size:20px;display:flex;align-items:center;justify-content:center}.drawer-body{padding:18px 20px 30px}.detail-section{border:1px solid var(--cr-line);border-radius:18px;padding:16px;margin-bottom:14px;background:var(--secondary-background-color)}.top{display:flex;justify-content:space-between;gap:14px}.detail-section h2{font-size:16px;margin:0 0 10px}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.detail-item{border-top:1px solid var(--cr-line);padding:8px 0;min-width:0}.detail-item:first-child,.detail-item:nth-child(2){border-top:0}.detail-label{color:var(--cr-muted);font-size:12px;margin-bottom:3px}.detail-value{font-weight:800;word-break:break-word}.timeline{margin:4px 0 0;padding:0;list-style:none}.timeline-item{position:relative;padding:0 0 18px 26px;border-left:2px solid var(--cr-line);margin-left:9px}.timeline-item:last-child{padding-bottom:0}.timeline-dot{position:absolute;left:-9px;top:0;width:16px;height:16px;border-radius:999px;background:var(--cr-accent);box-shadow:0 0 0 4px var(--card-background-color)}.timeline-status{font-weight:900;margin-bottom:3px}.timeline-meta{color:var(--cr-muted);font-size:12px;line-height:1.45}.timeline-normalized{display:inline-block;margin-top:5px;padding:3px 7px;border-radius:999px;background:rgba(158,158,158,.16);font-size:11px;font-weight:800;color:var(--cr-muted)}.mono{font-family:var(--code-font-family,monospace)}
      @media (prefers-color-scheme: light){.cr-field-row select{color-scheme:light}:host{--cr-bg:linear-gradient(135deg,#eaf6ff,#26a7bd);--cr-card:color-mix(in srgb,var(--card-background-color) 94%,#dff5fb 6%);--cr-shadow:0 18px 46px rgba(28,92,122,.16)}.cr-sort select{color-scheme:light}.cr-hero-main{color:#08324a}.cr-hero p{color:rgba(8,50,74,.76)}.cr-hero-badge{background:rgba(255,255,255,.84);color:#08324a;border-color:rgba(8,50,74,.12)}.cr-exit-button{background:rgba(255,255,255,.78);color:#08324a;border-color:rgba(8,50,74,.12)}.cr-logo{background:rgba(255,255,255,.78)}}
      @media (max-width:1050px){.cr-hero{grid-template-columns:1fr}.cr-hero-side{grid-template-columns:repeat(4,minmax(0,1fr))}.cr-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.cr-license-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.cr-settings-grid,.cr-test-grid,.cr-diagnostic-grid,.cr-courier-diagnostic-grid,.cr-notify-main,.cr-check-grid{grid-template-columns:1fr}}
      @media (max-width:760px){.cr-form-actions button{width:100%;justify-content:center}.cr-toolbar{grid-template-columns:1fr}.cr-toolbar-info{white-space:normal}.cr-sort select{min-width:0}.cr-panel{padding:14px 10px 70px}.cr-hero-main{min-height:190px;padding:22px;display:block}.cr-logo{width:66px;height:66px}.cr-logo img{border-radius:20px}.cr-hero h1{font-size:34px}.cr-exit-button{right:12px;top:12px;min-height:38px;padding:0 12px}.cr-exit-button span{display:none}.cr-hero-badge{right:12px;bottom:12px;grid-template-columns:30px auto;padding:7px 10px 7px 7px;font-size:11px}.cr-hero-badge img{width:30px;height:30px;border-radius:8px}.cr-hero-side{grid-template-columns:1fr 1fr}.cr-side-card{padding:16px}.cr-side-card strong{font-size:24px}.cr-tabs{display:flex}.cr-tab{min-width:58px;padding:0 14px}.cr-tab span{display:none}.cr-page{padding:16px;border-radius:22px}.cr-kpis,.cr-license-grid,.cr-card-grid{grid-template-columns:1fr}.cr-form-card form{grid-template-columns:1fr}.cr-action-row,.cr-section-head{display:block}.cr-action-row button,.cr-section-head button,.cr-form-card button{width:100%;justify-content:center;margin-top:12px}.cr-group-header{display:block}.cr-group-header strong{display:block;margin-top:8px}.cr-settings-card{grid-template-columns:1fr}.cr-settings-card button{width:100%;justify-content:center}.cr-license-support,.cr-contact-hero,.cr-contact-card{display:block}.cr-license-support-icon,.cr-contact-icon{margin-bottom:12px}.cr-contact-grid{grid-template-columns:1fr}.detail-grid{grid-template-columns:1fr}.detail-item:nth-child(2){border-top:1px solid var(--cr-line)}.drawer{width:100vw}.cr-license-hero{display:block}.cr-license-hero ha-icon{margin-top:14px}}
    `;
  }
}

customElements.define("curieri-romania-panel", CurieriRomaniaPanel);
