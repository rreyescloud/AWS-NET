# Prueba 4: Apagar ambos (debe fallar por safety rule)
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/routingcontrol/66fefc2441e440bd","RoutingControlState":"Off"},{"RoutingControlArn":"arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/routingcontrol/4c20c7d764c141cd","RoutingControlState":"Off"}]' \
  --region us-west-2 \
  --endpoint-url "https://d33ae326.route53-recovery-cluster.us-west-2.amazonaws.com/v1" \
  --profile rchiasro1

# Prueba 5: Mismo pero desde eu-west-1
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/routingcontrol/66fefc2441e440bd","RoutingControlState":"Off"},{"RoutingControlArn":"arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/routingcontrol/4c20c7d764c141cd","RoutingControlState":"Off"}]' \
  --region eu-west-1 \
  --endpoint-url "https://527f532c.route53-recovery-cluster.eu-west-1.amazonaws.com/v1" \
  --profile rchiasro1

# Prueba 6: Override safety rule (fuerza el OFF de ambos)
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/routingcontrol/66fefc2441e440bd","RoutingControlState":"Off"},{"RoutingControlArn":"arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/routingcontrol/4c20c7d764c141cd","RoutingControlState":"Off"}]' \
  --safety-rules-to-override '["arn:aws:route53-recovery-control::338797193989:controlpanel/d8c79dad966347c381851ab5438280b9/safetyrule/bcc59c21b50b4815"]' \
  --region us-west-2 \
  --endpoint-url "https://d33ae326.route53-recovery-cluster.us-west-2.amazonaws.com/v1" \
  --profile rchiasro1
