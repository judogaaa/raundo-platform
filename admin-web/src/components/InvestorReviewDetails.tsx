import { ExpandableText } from "./ExpandableText";
import { ReviewMedia } from "./ReviewMedia";

export type InvestorProfileView = {
  investor_type_label: string;
  strategy: string;
  cheque: string;
  sectors: string[];
  stages: string[];
  geography: string;
  support_modes: string[];
  position: string;
  public_reference_url: string;
  open_for_requests: boolean;
};

export type InvestorOrganization = {
  name: string;
  type: string;
  position: string;
  website_url: string;
  review_status: string;
  membership_status: string;
  affiliation_status: string;
  mandate_verified: boolean;
  mandate_label: string;
};

function Chips({ values }: { values: string[] }) {
  if (values.length === 0) return <span className="muted">Не указано</span>;
  return (
    <div className="chip-row">
      {values.map((v) => (
        <span key={v} className="chip">
          {v}
        </span>
      ))}
    </div>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="org-status">
      <span className="muted small">{label}</span>
      <span>{value || "Не указано"}</span>
    </div>
  );
}

/**
 * Investor-specific body of the moderation card: strategy, cheque,
 * preferences and the three independent organization verification states.
 */
export function InvestorReviewDetails({
  profile,
  organization,
  media,
}: {
  profile: InvestorProfileView;
  organization?: InvestorOrganization | null;
  media?: { position: number; kind: string; scan_state: string; preview_url?: string }[];
}) {
  return (
    <>
      <div className="review-columns">
        <div className="review-col">
          <div className="review-block">
            <h3>Инвестиционная стратегия</h3>
            <ExpandableText value={profile.strategy} limit={360} />
          </div>
          <div className="review-block">
            <h3>Инвестиционный чек</h3>
            <div className="cheque-value">{profile.cheque || "Не указан"}</div>
            <p className="muted small">
              {profile.open_for_requests ? "Открыт к входящим запросам" : "Запросы закрыты"}
            </p>
          </div>
          <div className="review-block">
            <h3>Материалы</h3>
            <ReviewMedia media={media} />
            <div className="review-value">
              <div className="muted field-key">Публичная ссылка</div>
              {profile.public_reference_url ? (
                <a
                  className="review-link"
                  href={profile.public_reference_url}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  {profile.public_reference_url}
                </a>
              ) : (
                <span className="muted">Не указано</span>
              )}
            </div>
          </div>
        </div>

        <div className="review-col">
          <div className="review-block">
            <h3>Инвестиционные предпочтения</h3>
            <div className="review-value">
              <div className="muted field-key">Отрасли</div>
              <Chips values={profile.sectors} />
            </div>
            <div className="review-value">
              <div className="muted field-key">Стадии стартапов</div>
              <Chips values={profile.stages} />
            </div>
            <div className="review-params">
              <div className="review-param">
                <div className="muted field-key">География</div>
                <div>{profile.geography || "Не указана"}</div>
              </div>
              <div className="review-param">
                <div className="muted field-key">Тип инвестора</div>
                <div>{profile.investor_type_label || "Не указано"}</div>
              </div>
            </div>
            <div className="review-value">
              <div className="muted field-key">Форматы сотрудничества</div>
              <Chips values={profile.support_modes} />
            </div>
          </div>

          <div className="review-block">
            <h3>Организация</h3>
            {!organization ? (
              <p className="review-text muted">Не заявлена</p>
            ) : (
              <>
                <div className="review-value">
                  <strong>{organization.name}</strong>
                  {organization.type && <div className="muted small">{organization.type}</div>}
                  {organization.position && (
                    <div className="muted small">Должность: {organization.position}</div>
                  )}
                  {organization.website_url && (
                    <a
                      className="review-link"
                      href={organization.website_url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {organization.website_url}
                    </a>
                  )}
                </div>
                <div className="org-statuses">
                  <StatusRow label="Проверка организации" value={organization.review_status} />
                  <StatusRow
                    label="Принадлежность представителя"
                    value={organization.membership_status || organization.affiliation_status}
                  />
                  <StatusRow label="Инвестиционный мандат" value={organization.mandate_label} />
                </div>
                {!organization.mandate_verified && (
                  <p className="mandate-warning">Полномочия не подтверждены</p>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
