# RFID card management and automatic charging

Before removing a card assigned to automatic charging, assign another stored card
or select **Charging card required**. Removing the assigned card, including by
clearing the entire list, is rejected until that assignment has been changed.
The integration reads this assignment from the charger rather than trusting the
last Home Assistant state.

Card responses must contain a complete, valid list. The Elvi numeric metadata
header and bracketed records are supported, including the observed empty response
`0,500,` and nonempty responses shaped like `1,500,[{...}]`. Optional card metadata is
accepted as a supported wire-format variation; only the card ID is exposed. A mismatch between the declared
card count and parsed records is rejected. Missing responses, malformed
records, trailing data, invalid IDs and duplicate IDs cause an error rather than
being interpreted as an empty list. A failed poll makes the entities unavailable
instead of publishing a false empty list.

Adding a card uses a differential update. The readback must contain the new card
and all previously read cards. Removing a card retains the firmware-compatible
full-list update, but rereads and compares the initial list in the same
connection as the version lookup, replacement and readback. If the list has
changed, refresh before retrying. Version rejection is reported as failure.
Explicitly clearing a list does not require the old list to remain unchanged;
it still protects an assigned AutoStart card and verifies the empty readback.
A verified clear is not followed by a redundant reconnect and full refresh.

These checks protect against malformed reads and changes observed between the
initial read and replacement. They do not make BLE and independent OCPP/backend
writers one atomic transaction, nor can they detect a firmware response that is
consistently incomplete but syntactically valid.

## Hardware investigation, 2026-11-18

The reported symptom was an apparently empty card list, automatic charging
waiting for authentication, and recovery after adding a card again. Re-adding
also enables `LocalAuthListEnabled`, so recovery alone does not prove deletion.

On an Elvi G4E-WBO running firmware
`P0425B0425v1.260323_W6.0.0-050`, two baseline test runs used the original 0.8.0
client/coordinator and an existing local hotfix that grouped full replacement
and readback in one connection. Temporary test cards survived additions,
AutoStart assignment and repeated polling. Removing another card preserved the
remaining card. Both implementations left `evb_AutoStart` referencing the
assigned card after it was removed: this inconsistent configuration was read
back directly from the physical charger.

After an accepted hard-reset request, the cards, assignment and local-list flag
were still present. No independent outage or BootNotification evidence was
recorded, so this does not establish power-cycle persistence. No vehicle was
connected and no electrical charging/authentication test was performed. The
original report of spontaneous deletion remains unconfirmed.

The candidate implementation is also exercised separately on the same idle
charger: add two cards, assign one to AutoStart, poll, remove the other card,
verify removal and clearing of the assigned card are rejected, switch back to
RFID authorization, then clear successfully. The initial empty list,
`evb_AutoStart=""`, and `LocalAuthListEnabled=true` are restored and independently
read back after testing. This is an isolated test process, not a deployed upgrade.

The offline regression suite additionally injects malformed and changed lists,
lost existing cards during an add, invalid list versions, rejected replacements,
and mismatched readbacks. These fault injections establish the handling of those
conditions; they are not claims that the physical charger produced them.
